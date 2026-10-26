"""Page script: caption region and chat log. Python polls the drain functions."""

PAGE_OBSERVER_JS = r"""
(() => {
  if (window.__baInstalled) return;
  window.__baInstalled = true;
  window.__baChatQueue = [];
  window.__baCaptionQueue = [];
  const seenChat = new Set();

  function region() {
    return document.querySelector('[role="region"][aria-label*="aption" i]')
      || document.querySelector("[data-ba-captions]");
  }

  function chatNodes() {
    const log = document.querySelector("[data-ba-chat-log]");
    if (!log) return;
    log.querySelectorAll("[data-chat-message]").forEach((node) => {
      const id = node.getAttribute("data-chat-message") || node.innerText;
      if (seenChat.has(id)) return;
      seenChat.add(id);
      window.__baChatQueue.push({
        sender: node.getAttribute("data-sender") || "",
        text: (node.innerText || "").trim(),
      });
    });
  }

  function attachChat() {
    const log = document.querySelector("[data-ba-chat-log]");
    if (!log || log.dataset.baWatching === "1") return;
    log.dataset.baWatching = "1";
    chatNodes();
    new MutationObserver(chatNodes).observe(log, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  }

  function captionSnapshot() {
    const root = region();
    if (!root) return;
    const rows = [];
    root.querySelectorAll("[data-row]").forEach((row) => {
      rows.push({
        row_id: row.getAttribute("data-row") || "",
        speaker: row.getAttribute("data-speaker") || "",
        text: (row.innerText || "").trim(),
      });
    });
    window.__baCaptionQueue.push({items: rows});
    if (window.__baCaptionQueue.length > 50) {
      window.__baCaptionQueue = window.__baCaptionQueue.slice(-50);
    }
  }

  function attachCaptions() {
    const root = region();
    if (!root || root.dataset.baWatching === "1") return;
    root.dataset.baWatching = "1";
    new MutationObserver(captionSnapshot).observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    captionSnapshot();
  }

  function scan() {
    attachChat();
    attachCaptions();
  }

  window.__baDrainChat = () => {
    scan();
    const queued = window.__baChatQueue.slice();
    window.__baChatQueue = [];
    return queued;
  };
  window.__baDrainCaptions = () => {
    scan();
    captionSnapshot();
    const queued = window.__baCaptionQueue.slice();
    window.__baCaptionQueue = [];
    return queued;
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
})();
"""
