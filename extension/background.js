const DEFAULT_SETTINGS = {
  workerUrl: "https://ba-meeting-agent.aliaizazco.workers.dev",
  webhookSecret: "",
  autoStart: true,
  triggerWhen: "open", // "open" (start container on Meet URL) or "joined" (wait until inside call)
  autoAdmit: true,
  botName: "TechSol BA",
};

const MEET_CODE_RE = /^\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/i;
const DEDUP_WINDOW_MS = 3 * 60 * 60 * 1000; // 3 hours (matches container sleepAfter)
const inFlightCodes = new Set();

function parseMeetCode(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return null;
  try {
    const parsed = new URL(rawUrl.trim());
    if (parsed.protocol !== "https:" || parsed.hostname.toLowerCase() !== "meet.google.com") {
      return null;
    }
    const match = MEET_CODE_RE.exec(parsed.pathname);
    return match ? match[1].toLowerCase() : null;
  } catch {
    return null;
  }
}

async function getSettings() {
  const stored = await chrome.storage.local.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

async function getJoinedMap() {
  const { joinedMeetings = {} } = await chrome.storage.local.get({ joinedMeetings: {} });
  return joinedMeetings;
}

async function saveJoinedMeeting(code, entry) {
  const joinedMeetings = await getJoinedMap();
  const now = Date.now();
  for (const [k, v] of Object.entries(joinedMeetings)) {
    if (!v || now - (v.timestamp || 0) > DEDUP_WINDOW_MS) {
      delete joinedMeetings[k];
    }
  }
  joinedMeetings[code] = { ...entry, timestamp: now };
  await chrome.storage.local.set({ joinedMeetings, lastSession: { code, ...entry, timestamp: now } });
}

function setBadge(tabId, text, color) {
  const target = tabId ? { tabId } : {};
  chrome.action.setBadgeText({ ...target, text });
  if (color) {
    chrome.action.setBadgeBackgroundColor({ ...target, color });
  }
}

function buildHeaders(webhookSecret) {
  const headers = { "Content-Type": "application/json" };
  if (webhookSecret && webhookSecret.trim()) {
    headers.Authorization = `Bearer ${webhookSecret.trim()}`;
  }
  return headers;
}

async function triggerJoin({ meetUrl, phase, force = false, tabId = null }) {
  const code = parseMeetCode(meetUrl);
  if (!code) {
    return { ok: false, error: "Not a valid Google Meet code URL" };
  }

  const settings = await getSettings();
  if (!force) {
    if (!settings.autoStart) {
      return { ok: false, skipped: true, reason: "Auto-start is disabled" };
    }
    if (settings.triggerWhen === "joined" && phase === "open") {
      return { ok: false, skipped: true, reason: "Waiting for call join" };
    }
  }

  const baseUrl = (settings.workerUrl || "").trim().replace(/\/+$/, "");
  if (!baseUrl) {
    setBadge(tabId, "CFG", "#6b7280");
    return {
      ok: false,
      needsConfig: true,
      error: "Set your Cloudflare Worker URL in the TechSol BA extension popup",
    };
  }

  const normalizedMeetUrl = `https://meet.google.com/${code}`;
  const joinedMeetings = await getJoinedMap();
  const existing = joinedMeetings[code];
  if (
    !force &&
    existing &&
    existing.status === "started" &&
    Date.now() - (existing.timestamp || 0) < DEDUP_WINDOW_MS
  ) {
    setBadge(tabId, "ON", "#16a34a");
    return {
      ok: true,
      alreadyStarted: true,
      meet_url: normalizedMeetUrl,
      code,
    };
  }

  if (inFlightCodes.has(code)) {
    return { ok: true, starting: true, meet_url: normalizedMeetUrl, code };
  }

  inFlightCodes.add(code);
  setBadge(tabId, "BOOT", "#d97706");
  await saveJoinedMeeting(code, {
    meet_url: normalizedMeetUrl,
    status: "starting",
  });

  try {
    const response = await fetch(`${baseUrl}/join`, {
      method: "POST",
      headers: buildHeaders(settings.webhookSecret),
      body: JSON.stringify({ url: normalizedMeetUrl }),
    });

    let data = {};
    try {
      data = await response.json();
    } catch {
      const text = await response.text();
      data = { error: text || `HTTP ${response.status}` };
    }

    if (!response.ok || data.ok === false) {
      const errorMsg = data.error || `Worker returned HTTP ${response.status}`;
      setBadge(tabId, "ERR", "#dc2626");
      await saveJoinedMeeting(code, {
        meet_url: normalizedMeetUrl,
        status: "error",
        error: errorMsg,
      });
      return { ok: false, error: errorMsg, code };
    }

    setBadge(tabId, "ON", "#16a34a");
    await saveJoinedMeeting(code, {
      meet_url: normalizedMeetUrl,
      status: "started",
      meeting_id: data.meeting_id || "",
      captions_enabled: Boolean(data.captions_enabled),
      chat_enabled: Boolean(data.chat_enabled),
    });

    return {
      ok: true,
      code,
      meet_url: normalizedMeetUrl,
      ...data,
    };
  } catch (error) {
    const errorMsg = error instanceof Error ? error.message : String(error);
    setBadge(tabId, "ERR", "#dc2626");
    await saveJoinedMeeting(code, {
      meet_url: normalizedMeetUrl,
      status: "error",
      error: errorMsg,
    });
    return { ok: false, error: errorMsg, code };
  } finally {
    inFlightCodes.delete(code);
  }
}

async function fetchWorkerStatus() {
  const settings = await getSettings();
  const baseUrl = (settings.workerUrl || "").trim().replace(/\/+$/, "");
  if (!baseUrl) {
    return { ok: false, error: "Worker URL not configured" };
  }
  try {
    const response = await fetch(`${baseUrl}/status?peek=1`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    const data = await response.json();
    return { ok: true, ...data };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== "string") {
    return false;
  }

  if (message.type === "MEET_DETECTED") {
    const tabId = sender.tab?.id ?? message.tabId ?? null;
    triggerJoin({
      meetUrl: message.url,
      phase: message.phase || "open",
      force: Boolean(message.force),
      tabId,
    }).then(sendResponse);
    return true;
  }

  if (message.type === "CHECK_STATUS") {
    fetchWorkerStatus().then(sendResponse);
    return true;
  }

  if (message.type === "GET_SETTINGS") {
    getSettings().then(sendResponse);
    return true;
  }

  return false;
});
