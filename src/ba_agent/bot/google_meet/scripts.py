"""Page script: caption region and chat log. Python polls the drain functions."""

PAGE_OBSERVER_JS = r"""
(() => {
  if (window.__baInstalled) return;
  window.__baInstalled = true;
  window.__baChatQueue = [];
  window.__baCaptionQueue = [];
  const seenChat = new Set();
  let nextRowId = 0;
  let nextChatId = 0;

  try {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  } catch (err) {}

  try {
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === "function") {
      const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = function () {
        return origGetUserMedia.apply(this, arguments).then((stream) => {
          if (stream && typeof stream.getTracks === "function") {
            stream.getTracks().forEach((track) => {
              track.enabled = false;
            });
          }
          return stream;
        });
      };
    }
  } catch (err) {}

  function region() {
    return document.querySelector('[role="region"][aria-label*="aption" i]')
      || document.querySelector("[data-ba-captions]");
  }

  function chatRoot() {
    return document.querySelector("[data-ba-chat-log]")
      || document.querySelector('[aria-live="polite"]');
  }

  function chatNodes() {
    const log = document.querySelector("[data-ba-chat-log]");
    if (log) {
      log.querySelectorAll("[data-chat-message]").forEach((node) => {
        const id = node.getAttribute("data-chat-message") || node.innerText;
        if (seenChat.has(id)) return;
        seenChat.add(id);
        window.__baChatQueue.push({
          sender: node.getAttribute("data-sender") || "",
          text: (node.innerText || "").trim(),
        });
      });
      return;
    }
    const sel = "[data-message-id], [data-message-text], .oIy2qc, [jsname='dTKtvb']";
    document.querySelectorAll(sel).forEach((node) => {
      if (!node.dataset.baChatId) {
        node.dataset.baChatId = String(++nextChatId);
      }
      const id = node.getAttribute("data-message-id") || node.dataset.baChatId;
      if (seenChat.has(id)) return;
      const text = (node.getAttribute("data-message-text") || node.innerText || "").trim();
      if (!text) return;
      seenChat.add(id);
      const group = node.closest("[data-sender-name], .Ss4fHf, .GDhqjd");
      let sender = "";
      if (group) {
        sender = group.getAttribute("data-sender-name") || "";
        if (!sender) {
          const badge = group.querySelector(".HNucUd, .poVWob, .yt1Zbd");
          if (badge) sender = (badge.innerText || "").trim();
        }
      }
      window.__baChatQueue.push({sender, text});
    });
  }

  function attachChat() {
    const log = chatRoot();
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
    const dataRows = root.querySelectorAll("[data-row]");
    if (dataRows.length > 0) {
      dataRows.forEach((row) => {
        rows.push({
          row_id: row.getAttribute("data-row") || "",
          speaker: row.getAttribute("data-speaker") || "",
          text: (row.innerText || "").trim(),
        });
      });
    } else {
      const blocks = root.querySelectorAll(".nMcdL, .TBMuR");
      blocks.forEach((block) => {
        if (!block.dataset.baRowId) {
          block.dataset.baRowId = String(++nextRowId);
        }
        const speakerEl = block.querySelector(".NWpY1d, .KcIKyf, .zs7s8d");
        const textEl = block.querySelector(".ygicle, .VbkSUe, .bh44bd, .iTTPOb, .CNusmb");
        const speaker = speakerEl ? (speakerEl.innerText || "").trim() : "";
        const text = textEl ? (textEl.innerText || "").trim() : "";
        if (text) {
          rows.push({
            row_id: block.dataset.baRowId,
            speaker,
            text,
          });
        }
      });
    }
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

# Keep this in step with ba_agent.bot.google_meet.video.reject_incoming_video_sdp.
REJECT_VIDEO_SDP_JS = r"""
function __baRejectVideoSdp(sdp) {
  if (sdp === "") return "";
  const newline = sdp.includes("\r\n") ? "\r\n" : "\n";
  const ended = sdp.endsWith("\n");
  const lines = sdp.split(/\r\n|\n|\r/);
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const out = [];
  let inVideo = false;
  let sawDirection = false;
  const directions = new Set(["a=sendrecv", "a=sendonly", "a=recvonly", "a=inactive"]);
  function finishSection() {
    if (inVideo && !sawDirection) out.push("a=inactive");
    sawDirection = false;
  }
  for (const line of lines) {
    if (line.startsWith("m=")) {
      finishSection();
      inVideo = line.startsWith("m=video");
      out.push(line);
      continue;
    }
    if (inVideo && directions.has(line.trim())) {
      if (!sawDirection) out.push("a=inactive");
      sawDirection = true;
      continue;
    }
    out.push(line);
  }
  finishSection();
  let result = out.join(newline);
  if (ended) result += newline;
  return result;
}
"""

REJECT_INCOMING_VIDEO_JS = (
    REJECT_VIDEO_SDP_JS
    + r"""
(() => {
  if (window.__baVideoOff) return;
  window.__baVideoOff = true;
  window.__baRejectVideoSdp = __baRejectVideoSdp;

  function rewrite(description) {
    if (!description || typeof description.sdp !== "string" || description.sdp.length === 0) {
      return description;
    }
    const sdp = __baRejectVideoSdp(description.sdp);
    if (sdp === description.sdp) return description;
    return {type: description.type, sdp: sdp};
  }

  const Proto = window.RTCPeerConnection && window.RTCPeerConnection.prototype;
  if (Proto && !Proto.__baVideoOff) {
    Proto.__baVideoOff = true;
    const origLocal = Proto.setLocalDescription;
    Proto.setLocalDescription = function (description) {
      if (description && typeof description.sdp === "string") {
        return origLocal.call(this, rewrite(description));
      }
      return origLocal.apply(this, arguments);
    };
    const origRemote = Proto.setRemoteDescription;
    Proto.setRemoteDescription = function (description) {
      if (description && typeof description.sdp === "string") {
        return origRemote.call(this, rewrite(description));
      }
      return origRemote.apply(this, arguments);
    };
    const origOffer = Proto.createOffer;
    Proto.createOffer = function () {
      return origOffer.apply(this, arguments).then(rewrite);
    };
    const origAnswer = Proto.createAnswer;
    Proto.createAnswer = function () {
      return origAnswer.apply(this, arguments).then(rewrite);
    };
  }

  try {
    const media = window.HTMLMediaElement && window.HTMLMediaElement.prototype;
    const desc = media && Object.getOwnPropertyDescriptor(media, "srcObject");
    if (media && desc && desc.set && desc.get && !media.__baVideoOff) {
      let stripping = false;
      Object.defineProperty(media, "srcObject", {
        configurable: true,
        enumerable: desc.enumerable,
        get: desc.get,
        set(value) {
          if (stripping || !value || typeof value.getVideoTracks !== "function") {
            desc.set.call(this, value);
            return;
          }
          const videos = value.getVideoTracks();
          if (videos.length === 0) {
            desc.set.call(this, value);
            return;
          }
          videos.forEach((track) => {
            track.enabled = false;
          });
          const audio = typeof value.getAudioTracks === "function" ? value.getAudioTracks() : [];
          stripping = true;
          try {
            desc.set.call(this, audio.length ? new MediaStream(audio) : null);
          } finally {
            stripping = false;
          }
        },
      });
      media.__baVideoOff = true;
    }
  } catch (err) {}
})();
"""
)
