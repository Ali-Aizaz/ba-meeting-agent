(() => {
  const MEET_CODE_RE = /^(?:\/_?meet)?\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/i;
  let lastNotifiedOpenCode = null;
  let lastNotifiedJoinedCode = null;
  let toastTimeout = null;
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

  function showToast(text, tone = "info") {
    let el = document.getElementById("techsol-ba-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "techsol-ba-toast";
      Object.assign(el.style, {
        position: "fixed",
        bottom: "20px",
        right: "20px",
        zIndex: "2147483647",
        padding: "10px 14px",
        borderRadius: "8px",
        fontFamily: "system-ui, -apple-system, sans-serif",
        fontSize: "13px",
        fontWeight: "500",
        color: "#f8fafc",
        boxShadow: "0 10px 25px rgba(0, 0, 0, 0.35)",
        transition: "opacity 0.25s ease",
        pointerEvents: "none",
      });
      document.body.appendChild(el);
    }

    const backgrounds = {
      info: "#1e293b",
      success: "#15803d",
      warn: "#b45309",
      error: "#b91c1c",
    };
    el.style.background = backgrounds[tone] || backgrounds.info;
    el.style.opacity = "1";
    el.textContent = text;

    if (toastTimeout) clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => {
      if (el) el.style.opacity = "0";
    }, 7000);
  }

  function notifyBackground(code, phase) {
    const url = `https://meet.google.com/${code}`;
    const shouldTrigger =
      cachedSettings.autoStart &&
      ((phase === "open" && cachedSettings.triggerWhen === "open") ||
        (phase === "joined" && cachedSettings.triggerWhen === "joined"));
    if (shouldTrigger) {
      showToast("TechSol BA: Starting Cloudflare bot for this Meet…", "info");
    }
    chrome.runtime.sendMessage(
      {
        type: "MEET_DETECTED",
        url,
        code,
        phase,
      },
      (response) => {
        if (chrome.runtime.lastError || !response) return;
        if (response.skipped) return;
        if (response.needsConfig) {
          showToast("TechSol BA: Click the extension icon to set your Worker URL", "warn");
          return;
        }
        if (!response.ok) {
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

    if (code !== lastNotifiedOpenCode) {
      lastNotifiedOpenCode = code;
      notifyBackground(code, "open");
    }

    if (code !== lastNotifiedJoinedCode && isInActiveCall()) {
      lastNotifiedJoinedCode = code;
      notifyBackground(code, "joined");
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
      if (!matchedName) continue;

      const buttons = dialog.querySelectorAll('button, [role="button"]');
      for (const btn of buttons) {
        if (isAdmitButton(btn)) {
          btn.click();
          showToast(`TechSol BA: Auto-admitted ${cachedSettings.botName || "BA Assistant"} ✓`, "success");
          return;
        }
      }
    }

    const allButtons = document.querySelectorAll('button, [role="button"]');
    for (const btn of allButtons) {
      if (isAdmitButton(btn)) {
        const container =
          btn.closest('[role="dialog"], [role="alertdialog"], [aria-modal="true"], [aria-live], [jscontroller]') ||
          document.body;
        const containerText = (container.innerText || container.textContent || "").toLowerCase();
        if (botNames.some((name) => containerText.includes(name))) {
          btn.click();
          showToast(`TechSol BA: Auto-admitted ${cachedSettings.botName || "BA Assistant"} ✓`, "success");
          return;
        }
      }
    }
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
  }, 1500);
})();
