(() => {
  const MEET_CODE_RE = /^(?:\/_?meet)?\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/i;
  let lastNotifiedOpenCode = null;
  let lastNotifiedJoinedCode = null;
  let toastTimeout = null;
  let lastActivitySeq = 0;
  let statusPollInFlight = false;
  let lastStatusPollAt = 0;
  let fastPollUntil = 0;
  let lastLocalTriggerText = "";
  let lastLocalTriggerAt = 0;
  let lastNotifyAttemptAt = 0;
  let lastViewKnockingClickAt = 0;
  const recentTrail = [];
  let cachedSettings = {
    autoStart: true,
    triggerWhen: "open",
    autoAdmit: true,
    botName: "TechSol BA",
  };

  function getMeetCode() {
    const match = MEET_CODE_RE.exec(window.location.pathname);
    return match ? match[1].toLowerCase() : null;
  }

  function isInActiveCall() {
    const leaveButtons = document.querySelectorAll(
      'button[aria-label*="Leave call" i], [role="button"][aria-label*="Leave call" i]',
    );
    return leaveButtons.length > 0;
  }

  function ensureToastStyle() {
    if (document.getElementById("techsol-ba-toast-style")) return;
    const style = document.createElement("style");
    style.id = "techsol-ba-toast-style";
    style.textContent = `
      @keyframes techsol-ba-spin {
        to { transform: rotate(360deg); }
      }
      #techsol-ba-toast {
        position: fixed;
        bottom: 20px;
        right: 20px;
        z-index: 2147483647;
        max-width: 390px;
        padding: 11px 14px;
        border-radius: 10px;
        font-family: system-ui, -apple-system, sans-serif;
        font-size: 13px;
        font-weight: 500;
        color: #f8fafc;
        box-shadow: 0 12px 28px rgba(0, 0, 0, 0.4);
        border: 1px solid rgba(148, 163, 184, 0.25);
        transition: opacity 0.25s ease;
        pointer-events: none;
      }
      #techsol-ba-toast .ba-toast-main {
        display: flex;
        align-items: center;
        gap: 9px;
        font-weight: 600;
        line-height: 1.35;
      }
      #techsol-ba-toast .ba-toast-spinner {
        width: 14px;
        height: 14px;
        border: 2px solid rgba(248, 250, 252, 0.28);
        border-top-color: #38bdf8;
        border-radius: 50%;
        animation: techsol-ba-spin 0.75s linear infinite;
        flex-shrink: 0;
      }
      #techsol-ba-toast .ba-toast-trail {
        margin-top: 5px;
        font-size: 11px;
        font-weight: 400;
        color: #cbd5e1;
        opacity: 0.85;
        line-height: 1.35;
      }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function showToast(text, tone = "info", trail = []) {
    if (!document.body) return;
    ensureToastStyle();
    let el = document.getElementById("techsol-ba-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "techsol-ba-toast";
      document.body.appendChild(el);
    }

    const backgrounds = {
      working: "#0f172a",
      info: "#1e293b",
      success: "#15803d",
      warn: "#b45309",
      error: "#b91c1c",
    };
    el.style.background = backgrounds[tone] || backgrounds.info;
    el.style.opacity = "1";
    el.textContent = "";

    const main = document.createElement("div");
    main.className = "ba-toast-main";
    if (tone === "working") {
      const spinner = document.createElement("div");
      spinner.className = "ba-toast-spinner";
      main.appendChild(spinner);
    }
    const label = document.createElement("span");
    const prefixed = text.startsWith("TechSol BA") ? text : `TechSol BA: ${text}`;
    label.textContent = prefixed;
    main.appendChild(label);
    el.appendChild(main);

    if (Array.isArray(trail) && trail.length > 0) {
      const sub = document.createElement("div");
      sub.className = "ba-toast-trail";
      sub.textContent = trail.join(" → ");
      el.appendChild(sub);
    }

    if (toastTimeout) clearTimeout(toastTimeout);
    const ttl = tone === "working" ? 25000 : 6500;
    toastTimeout = setTimeout(() => {
      if (el) el.style.opacity = "0";
      if (tone !== "working") recentTrail.length = 0;
    }, ttl);
  }

  function parseCodeFromUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== "string") return null;
    try {
      const parsed = new URL(rawUrl.trim());
      const match = MEET_CODE_RE.exec(parsed.pathname);
      return match ? match[1].toLowerCase() : null;
    } catch {
      return null;
    }
  }

  function notifyBackground(code, phase, options = {}) {
    const force = Boolean(options.force);
    const url = `https://meet.google.com/${code}`;
    lastNotifyAttemptAt = Date.now();
    const shouldTrigger =
      force ||
      (cachedSettings.autoStart &&
        ((phase === "open" && cachedSettings.triggerWhen === "open") ||
          (phase === "joined" && cachedSettings.triggerWhen === "joined")));
    if (shouldTrigger && !options.silent) {
      showToast("TechSol BA: Starting Cloudflare bot for this Meet…", "info");
    }
    chrome.runtime.sendMessage(
      {
        type: "MEET_DETECTED",
        url,
        code,
        phase,
        force,
      },
      (response) => {
        if (chrome.runtime.lastError || !response) {
          if (phase === "open") lastNotifiedOpenCode = null;
          if (phase === "joined") lastNotifiedJoinedCode = null;
          return;
        }
        if (response.skipped) return;
        if (response.needsConfig) {
          showToast("TechSol BA: Click the extension icon to set your Worker URL", "warn");
          return;
        }
        if (!response.ok) {
          if (phase === "open") lastNotifiedOpenCode = null;
          if (phase === "joined") lastNotifiedJoinedCode = null;
          showToast(`TechSol BA error: ${response.error || "Failed to start"}`, "error");
          return;
        }
        if (!response.alreadyStarted) {
          showToast("TechSol BA: Bot container started and joining call ✓", "success");
        }
      },
    );
  }

  function checkMeetState() {
    const code = getMeetCode();
    if (!code) {
      lastNotifiedOpenCode = null;
      lastNotifiedJoinedCode = null;
      return;
    }

    const now = Date.now();
    if (code !== lastNotifiedOpenCode && now - lastNotifyAttemptAt > 2500) {
      lastNotifiedOpenCode = code;
      notifyBackground(code, "open");
    }

    if (isInActiveCall()) {
      if (code !== lastNotifiedJoinedCode) {
        lastNotifiedJoinedCode = code;
        notifyBackground(code, "joined");
      }
    } else {
      lastNotifiedJoinedCode = null;
    }
  }

  function tryAutoAdmitBot() {
    if (!cachedSettings.autoAdmit) return;
    const configuredName = (cachedSettings.botName || "TechSol BA").trim().toLowerCase();
    const botNames = Array.from(
      new Set([configuredName, "techsol ba", "ba assistant", "baassistant703"].filter(Boolean)),
    );
    if (botNames.length === 0) return;

    function isAdmitButton(btn) {
      const text = (btn.innerText || btn.textContent || "").trim().toLowerCase();
      const aria = (btn.getAttribute("aria-label") || "").trim().toLowerCase();
      return (
        text === "admit" ||
        text.endsWith("\nadmit") ||
        aria === "admit" ||
        aria.startsWith("admit ") ||
        botNames.some((name) => text === `admit ${name}` || aria === `admit ${name}`)
      );
    }

    const dialogs = document.querySelectorAll(
      '[role="dialog"], [role="alertdialog"], [aria-modal="true"], [aria-live]',
    );
    for (const dialog of dialogs) {
      const dialogText = (dialog.innerText || dialog.textContent || "").toLowerCase();
      const matchedName = botNames.find((name) => dialogText.includes(name));
      const isGenericKnock =
        dialogText.includes("wants to join") || dialogText.includes("waiting to be let in");
      if (!matchedName && !isGenericKnock) continue;

      const buttons = dialog.querySelectorAll('button, [role="button"]');
      for (const btn of buttons) {
        if (isAdmitButton(btn)) {
          btn.click();
          showToast(`TechSol BA: Auto-admitted ${cachedSettings.botName || "BA Assistant"} ✓`, "success");
          return;
        }
      }
      if (isGenericKnock && Date.now() - lastViewKnockingClickAt > 5000) {
        for (const btn of buttons) {
          const text = (btn.innerText || btn.textContent || "").trim().toLowerCase();
          const aria = (btn.getAttribute("aria-label") || "").trim().toLowerCase();
          if (text === "view" || text.endsWith("\nview") || aria === "view" || aria === "view all") {
            lastViewKnockingClickAt = Date.now();
            btn.click();
            return;
          }
        }
      }
    }

    const allButtons = document.querySelectorAll('button, [role="button"]');
    for (const btn of allButtons) {
      if (isAdmitButton(btn)) {
        let cursor = btn.parentElement;
        for (let depth = 0; depth < 7 && cursor && cursor !== document.body; depth++) {
          const containerText = (cursor.innerText || cursor.textContent || "").toLowerCase();
          if (botNames.some((name) => containerText.includes(name))) {
            btn.click();
            showToast(`TechSol BA: Auto-admitted ${cachedSettings.botName || "BA Assistant"} ✓`, "success");
            return;
          }
          cursor = cursor.parentElement;
        }
      }
    }
  }

  const WAKE_RE =
    /\b(?:hey|hi|ok|okay|hello|yo)\s*,?\s*(?:techsol\s+)?(?:ba|b\.a\.|bea|va|bia)\b|^@ba\b/i;
  const LOOKALIKE_RE = /\b(?:hey\s+(?:bay|bob|bae)|bay\s+area|the\s+ba\s+team)\b/i;

  function detectLocalWakePhrase() {
    if (!isInActiveCall()) return;
    const candidates = [];
    const captionRoot = document.querySelector('[role="region"][aria-label*="aption" i]');
    if (captionRoot) {
      const blocks = captionRoot.querySelectorAll(".ygicle, .VbkSUe, .bh44bd, .iTTPOb, .CNusmb, .nMcdL");
      if (blocks.length > 0) {
        const lastBlock = blocks[blocks.length - 1];
        candidates.push((lastBlock.innerText || "").trim());
      } else {
        candidates.push((captionRoot.innerText || "").trim());
      }
    }
    const chatNodes = document.querySelectorAll("[jsname='dTKtvb'], [data-message-text], .oIy2qc");
    if (chatNodes.length > 0) {
      const lastChat = chatNodes[chatNodes.length - 1];
      candidates.push(
        (lastChat.getAttribute("data-message-text") || lastChat.innerText || "").trim(),
      );
    }

    const now = Date.now();
    for (const raw of candidates) {
      if (!raw || LOOKALIKE_RE.test(raw)) continue;
      if (WAKE_RE.test(raw)) {
        const snippet = raw.slice(-80);
        if (snippet !== lastLocalTriggerText || now - lastLocalTriggerAt > 5000) {
          lastLocalTriggerText = snippet;
          lastLocalTriggerAt = now;
          fastPollUntil = now + 20000;
          showToast(`Heard "${snippet}" — processing…`, "working");
          pollBotActivity(true);
        }
        break;
      }
    }
  }

  function pollBotActivity(force = false) {
    if (!isInActiveCall() || statusPollInFlight) return;
    const now = Date.now();
    const interval = now < fastPollUntil ? 1000 : 2800;
    if (!force && now - lastStatusPollAt < interval) return;
    statusPollInFlight = true;
    lastStatusPollAt = now;

    chrome.runtime.sendMessage({ type: "CHECK_STATUS" }, (response) => {
      statusPollInFlight = false;
      if (chrome.runtime.lastError || !response || !response.ok) return;

      const currentCode = getMeetCode();
      if (currentCode && cachedSettings.autoStart && isInActiveCall()) {
        const botCode = parseCodeFromUrl(response.meet_url);
        const needsSummon =
          Boolean(response.idle) ||
          response.stage === "idle" ||
          response.stage === "failed" ||
          (Boolean(botCode) && botCode !== currentCode);
        if (needsSummon && Date.now() - lastNotifyAttemptAt > 12000) {
          notifyBackground(currentCode, "joined", { force: true, silent: true });
          return;
        }
      }

      if (response.idle) return;

      const events = Array.isArray(response.events) ? response.events : [];
      const unseen = events.filter((ev) => ev && typeof ev.seq === "number" && ev.seq > lastActivitySeq);
      if (unseen.length > 0) {
        for (const ev of unseen) {
          lastActivitySeq = Math.max(lastActivitySeq, ev.seq);
          if (ev.text && recentTrail[recentTrail.length - 1] !== ev.text) {
            recentTrail.push(ev.text);
            if (recentTrail.length > 3) recentTrail.shift();
          }
        }
        const latest = unseen[unseen.length - 1];
        const activeText = response.activity || latest.text || "";
        const tone = response.activity ? response.activity_kind || "working" : latest.kind || "info";
        if (tone === "working") {
          fastPollUntil = Date.now() + 15000;
        }
        if (activeText) {
          const trail = recentTrail.slice(0, -1);
          showToast(activeText, tone, trail);
        }
      } else if (response.activity) {
        fastPollUntil = Date.now() + 10000;
        showToast(
          response.activity,
          response.activity_kind || "working",
          recentTrail.slice(0, -1),
        );
      }
    });
  }

  chrome.runtime.sendMessage({ type: "GET_SETTINGS" }, (settings) => {
    if (!chrome.runtime.lastError && settings) {
      cachedSettings = { ...cachedSettings, ...settings };
    }
    checkMeetState();
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    for (const [key, change] of Object.entries(changes)) {
      if (key in cachedSettings) {
        cachedSettings[key] = change.newValue;
      }
    }
  });

  setInterval(() => {
    checkMeetState();
    tryAutoAdmitBot();
    detectLocalWakePhrase();
    pollBotActivity();
  }, 1000);
})();
