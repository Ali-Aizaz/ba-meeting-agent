const DEFAULT_SETTINGS = {
  workerUrl: "https://ba-meeting-agent.aliaizazco.workers.dev",
  webhookSecret: "",
  autoStart: true,
  triggerWhen: "open",
  autoAdmit: true,
  botName: "TechSol BA",
};

const workerUrlInput = document.getElementById("workerUrl");
const webhookSecretInput = document.getElementById("webhookSecret");
const triggerWhenSelect = document.getElementById("triggerWhen");
const autoStartCheckbox = document.getElementById("autoStart");
const autoAdmitCheckbox = document.getElementById("autoAdmit");
const saveBtn = document.getElementById("saveBtn");
const summonBtn = document.getElementById("summonBtn");
const statusBox = document.getElementById("statusBox");
const activityBox = document.getElementById("activityBox");

function setStatus(text) {
  statusBox.textContent = text;
}

function renderActivity(response) {
  if (!activityBox) return;
  if (!response || !response.ok || response.idle) {
    activityBox.textContent = "Activity: Waiting for active meeting…";
    return;
  }
  const lines = [];
  if (response.activity) {
    lines.push(`⏳ Working: ${response.activity}`);
  }
  const events = Array.isArray(response.events) ? response.events : [];
  for (const ev of events.slice(-6).reverse()) {
    if (ev && ev.text && ev.text !== response.activity) {
      const icon =
        ev.kind === "success"
          ? "✓"
          : ev.kind === "error"
            ? "✗"
            : ev.kind === "working"
              ? "⋯"
              : "•";
      lines.push(`${icon} ${ev.text}`);
    }
  }
  activityBox.textContent =
    lines.length > 0 ? lines.join("\n") : "Activity: Listening in call (say 'Hey BA…')";
}

function refreshWorkerStatus(sessionSummary = "") {
  chrome.runtime.sendMessage({ type: "CHECK_STATUS" }, (response) => {
    if (chrome.runtime.lastError || !response) return;
    if (!response.ok) {
      setStatus(
        sessionSummary
          ? `Worker unreachable (${response.error || "error"}) | ${sessionSummary}`
          : `Worker error: ${response.error || "unreachable"}`,
      );
      return;
    }
    if (response.idle) {
      setStatus(
        sessionSummary
          ? `Worker connected ✓ (Idle) | ${sessionSummary}`
          : "Worker connected ✓ (Idle — open a Google Meet to auto-start)",
      );
    } else {
      setStatus(
        `Worker active ✓ | Meet: ${response.meet_url || "active"} | captions: ${Boolean(response.captions_enabled)} | chat: ${Boolean(response.chat_enabled)}`,
      );
    }
    renderActivity(response);
  });
}

async function loadPopup() {
  const stored = await chrome.storage.local.get({
    ...DEFAULT_SETTINGS,
    lastSession: null,
  });
  workerUrlInput.value = stored.workerUrl || "";
  webhookSecretInput.value = stored.webhookSecret || "";
  triggerWhenSelect.value = stored.triggerWhen || "open";
  autoStartCheckbox.checked = Boolean(stored.autoStart);
  autoAdmitCheckbox.checked = Boolean(stored.autoAdmit);

  if (!stored.workerUrl) {
    setStatus("Enter your Cloudflare Worker URL above and click Save Settings.");
    return;
  }

  let sessionSummary = "";
  if (stored.lastSession) {
    const s = stored.lastSession;
    sessionSummary = [
      `Last Meet: ${s.code || s.meet_url || "none"}`,
      `State: ${s.status || "unknown"}`,
      s.error ? `Error: ${s.error}` : "",
    ]
      .filter(Boolean)
      .join(" | ");
    setStatus(sessionSummary);
  } else {
    setStatus("Checking Cloudflare Worker status…");
  }

  refreshWorkerStatus(sessionSummary);
  setInterval(() => refreshWorkerStatus(sessionSummary), 2000);
}

async function saveSettings() {
  const workerUrl = workerUrlInput.value.trim().replace(/\/+$/, "");
  const webhookSecret = webhookSecretInput.value.trim();
  const triggerWhen = triggerWhenSelect.value;
  const autoStart = autoStartCheckbox.checked;
  const autoAdmit = autoAdmitCheckbox.checked;

  await chrome.storage.local.set({
    workerUrl,
    webhookSecret,
    triggerWhen,
    autoStart,
    autoAdmit,
  });
  setStatus("Settings saved ✓");
}

saveBtn.addEventListener("click", () => {
  saveSettings();
});

summonBtn.addEventListener("click", async () => {
  await saveSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !tab.url.startsWith("https://meet.google.com/")) {
    setStatus("Open a Google Meet tab first (https://meet.google.com/xxx-xxxx-xxx).");
    return;
  }

  setStatus("Starting Cloudflare container for this Meet (can take 15–30s on cold boot)…");
  chrome.runtime.sendMessage(
    {
      type: "MEET_DETECTED",
      url: tab.url,
      phase: "joined",
      force: true,
      tabId: tab.id,
    },
    (response) => {
      if (chrome.runtime.lastError) {
        setStatus(`Error: ${chrome.runtime.lastError.message}`);
        return;
      }
      if (!response || !response.ok) {
        setStatus(`Error: ${response?.error || "Failed to start container"}`);
        return;
      }
      setStatus(
        `Active on ${response.meet_url} | captions: ${Boolean(response.captions_enabled)} | chat: ${Boolean(response.chat_enabled)}`,
      );
    },
  );
});

loadPopup();
