"""Page script: caption region and chat log. Python polls the drain functions."""

PAGE_OBSERVER_JS = r"""
(() => {
  if (window.__baInstalled) return;
  window.__baInstalled = true;
  window.__baChatQueue = [];
  window.__baCaptionQueue = [];
  const seenChat = new Set();
  const recentChatText = new Map();
  let nextRowId = 0;
  let nextChatId = 0;

  try {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  } catch (err) {}

  try {
    if (typeof window !== "undefined" && "speechSynthesis" in window && window.speechSynthesis) {
      window.speechSynthesis.speak = function () {};
      window.speechSynthesis.cancel = function () {};
    }
  } catch (err) {}

  try {
    function silenceTrack(track) {
      if (!track) return;
      try {
        track.enabled = false;
        Object.defineProperty(track, "enabled", {
          configurable: true,
          enumerable: true,
          get: () => false,
          set: () => false,
        });
      } catch (err) {}
    }

    function createBlankVideoTrack() {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 2;
        canvas.height = 2;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.fillStyle = "#000000";
          ctx.fillRect(0, 0, 2, 2);
        }
        if (typeof canvas.captureStream === "function") {
          const stream = canvas.captureStream(0);
          const tracks =
            stream && typeof stream.getVideoTracks === "function"
              ? stream.getVideoTracks()
              : [];
          if (tracks.length > 0) {
            if (typeof tracks[0].stop === "function") tracks[0].stop();
            silenceTrack(tracks[0]);
            return tracks[0];
          }
        }
      } catch (err) {}
      return null;
    }

    const isMeetHost =
      typeof window !== "undefined" &&
      window.location &&
      window.location.hostname === "meet.google.com";

    if (
      isMeetHost &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.enumerateDevices === "function"
    ) {
      const origEnumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
      navigator.mediaDevices.enumerateDevices = function () {
        return origEnumerate.apply(this, arguments).then((devices) =>
          Array.isArray(devices)
            ? devices.filter((d) => !d || (d.kind !== "videoinput" && d.kind !== "audiooutput"))
            : devices
        );
      };
    }

    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === "function") {
      const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = function (constraints) {
        if (isMeetHost && constraints && typeof constraints === "object") {
          if (constraints.video && !constraints.audio) {
            return Promise.reject(
              new DOMException("Requested device not found", "NotFoundError")
            );
          }
          if (constraints.video && constraints.audio) {
            constraints = { audio: constraints.audio };
          }
        }
        return origGetUserMedia.call(this, constraints).then((stream) => {
          if (stream && typeof stream.getVideoTracks === "function") {
            stream.getVideoTracks().forEach((track) => {
              silenceTrack(track);
              const blank = createBlankVideoTrack();
              if (
                blank &&
                typeof stream.removeTrack === "function" &&
                typeof stream.addTrack === "function"
              ) {
                try {
                  stream.removeTrack(track);
                  if (typeof track.stop === "function") track.stop();
                  if (!isMeetHost) {
                    stream.addTrack(blank);
                  }
                } catch (err) {}
              } else if (typeof track.stop === "function") {
                try {
                  track.stop();
                } catch (err) {}
              }
            });
          }
          if (stream && typeof stream.getTracks === "function") {
            stream.getTracks().forEach(silenceTrack);
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
      || document.querySelector('[jsname="xySENc"]')
      || document.querySelector('.Ge9Kpc[aria-live="polite"]')
      || document.querySelector('[aria-live="polite"]:not([data-mdc-dom-announce])');
  }

  function extractChatSender(group) {
    if (!group) return "";
    if (group.classList && group.classList.contains("ydIQ1d")) {
      return "You";
    }
    let sender = (group.getAttribute("data-sender-name") || "").trim();
    if (sender) return sender;
    const badge = group.querySelector(".poVWob, .yt1Zbd, .zWGUib");
    if (badge) {
      sender = (badge.innerText || "").trim();
      if (sender) return sender;
    }
    const header = group.querySelector(".HNucUd");
    if (header) {
      const clone = header.cloneNode(true);
      clone.querySelectorAll(".MuzmKe, [jsname='biJjHb']").forEach((el) => el.remove());
      const cleaned = (clone.innerText || "")
        .split("\n")
        .map((s) => s.trim())
        .filter((s) => s && !/^\d{1,2}:\d{2}(\s*[AP]M)?$/i.test(s))
        .join(" ");
      if (cleaned) return cleaned;
      if (header.querySelector(".MuzmKe, [jsname='biJjHb']")) {
        return "You";
      }
    }
    return "";
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
    const root = chatRoot() || document;
    let nodes = Array.from(root.querySelectorAll("[jsname='dTKtvb']"));
    if (nodes.length === 0) {
      nodes = Array.from(root.querySelectorAll("[data-message-text], .oIy2qc"));
    }
    if (nodes.length === 0) {
      nodes = Array.from(
        root.querySelectorAll("[data-message-id]:not(button):not([role='button'])")
      );
    }
    const now = Date.now();
    nodes.forEach((node) => {
      const textTarget =
        node.getAttribute && node.getAttribute("jsname") === "dTKtvb"
          ? node
          : node.querySelector
          ? node.querySelector("[jsname='dTKtvb']") || node
          : node;
      const wrapper =
        (textTarget.closest &&
          textTarget.closest("[data-message-id], .RLrADb, .Be7nke")) ||
        textTarget;
      const idEl =
        wrapper.getAttribute && wrapper.getAttribute("data-message-id")
          ? wrapper
          : wrapper.querySelector
          ? wrapper.querySelector("[data-message-id]")
          : null;
      const msgId =
        (idEl && idEl.getAttribute && idEl.getAttribute("data-message-id")) || "";
      if (!wrapper.dataset.baChatId) {
        wrapper.dataset.baChatId = String(++nextChatId);
      }
      const localId = wrapper.dataset.baChatId;
      if (seenChat.has(localId) || (msgId && seenChat.has(msgId))) {
        seenChat.add(localId);
        if (msgId) seenChat.add(msgId);
        return;
      }
      const text = (
        textTarget.getAttribute("data-message-text") ||
        textTarget.innerText ||
        ""
      ).trim();
      if (!text) return;
      seenChat.add(localId);
      if (msgId) seenChat.add(msgId);
      const group = textTarget.closest("[data-sender-name], .Ss4fHf, .GDhqjd");
      const sender = extractChatSender(group);
      const dedupKey = `${sender}\0${text}`;
      const prevAt = recentChatText.get(dedupKey);
      if (prevAt !== undefined && now - prevAt < 2500) {
        return;
      }
      recentChatText.set(dedupKey, now);
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
      let blocks = Array.from(root.querySelectorAll(".nMcdL, .TBMuR"));
      if (blocks.length === 0) {
        blocks = Array.from(root.children).filter(
          (el) =>
            el.tagName === "DIV" &&
            !el.querySelector('button[aria-label*="Jump" i]') &&
            (el.innerText || "").trim().length > 0
        );
      }
      blocks.forEach((block) => {
        if (!block.dataset.baRowId) {
          block.dataset.baRowId = String(++nextRowId);
        }
        const speakerEl = block.querySelector(".NWpY1d, .KcIKyf, .zs7s8d");
        const textEl = block.querySelector(".ygicle, .VbkSUe, .bh44bd, .iTTPOb, .CNusmb");
        let speaker = speakerEl ? (speakerEl.innerText || "").trim() : "";
        let text = textEl ? (textEl.innerText || "").trim() : "";
        if (!text && !speakerEl && !textEl) {
          const lines = (block.innerText || "")
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean);
          if (lines.length >= 2) {
            speaker = lines[0];
            text = lines.slice(1).join(" ");
          } else if (lines.length === 1) {
            text = lines[0];
          }
        }
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

  let toastTimer = null;
  const recentSteps = [];

  window.__baShowToast = (text, kind = "info") => {
    try {
      if (!document.body) return;
      if (!document.getElementById("ba-agent-toast-style")) {
        const style = document.createElement("style");
        style.id = "ba-agent-toast-style";
        style.textContent = `
          @keyframes ba-toast-spin {
            to { transform: rotate(360deg); }
          }
          #ba-agent-toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 2147483647;
            max-width: 380px;
            padding: 11px 14px;
            border-radius: 10px;
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 13px;
            color: #f8fafc;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(148, 163, 184, 0.25);
            transition: opacity 0.25s ease, transform 0.2s ease;
            pointer-events: none;
          }
          #ba-agent-toast .ba-toast-row {
            display: flex;
            align-items: center;
            gap: 9px;
            font-weight: 600;
            line-height: 1.35;
          }
          #ba-agent-toast .ba-toast-spinner {
            width: 14px;
            height: 14px;
            border: 2px solid rgba(248, 250, 252, 0.3);
            border-top-color: #38bdf8;
            border-radius: 50%;
            animation: ba-toast-spin 0.75s linear infinite;
            flex-shrink: 0;
          }
          #ba-agent-toast .ba-toast-sub {
            margin-top: 5px;
            font-size: 11px;
            color: #cbd5e1;
            opacity: 0.85;
            line-height: 1.35;
          }
        `;
        (document.head || document.documentElement).appendChild(style);
      }

      let el = document.getElementById("ba-agent-toast");
      if (!el) {
        el = document.createElement("div");
        el.id = "ba-agent-toast";
        document.body.appendChild(el);
      }

      const cleaned = String(text || "").trim();
      if (!cleaned) {
        el.style.opacity = "0";
        return;
      }

      if (recentSteps[recentSteps.length - 1] !== cleaned) {
        recentSteps.push(cleaned);
        if (recentSteps.length > 3) recentSteps.shift();
      }

      const backgrounds = {
        working: "#0f172a",
        info: "#1e293b",
        success: "#14532d",
        warn: "#78350f",
        error: "#7f1d1d",
      };
      el.style.background = backgrounds[kind] || backgrounds.info;
      el.style.opacity = "1";

      el.textContent = "";
      const row = document.createElement("div");
      row.className = "ba-toast-row";
      if (kind === "working") {
        const spin = document.createElement("div");
        spin.className = "ba-toast-spinner";
        row.appendChild(spin);
      }
      const label = document.createElement("span");
      label.textContent = `TechSol BA: ${cleaned}`;
      row.appendChild(label);
      el.appendChild(row);

      if (recentSteps.length > 1) {
        const sub = document.createElement("div");
        sub.className = "ba-toast-sub";
        sub.textContent = recentSteps.slice(0, -1).join(" → ");
        el.appendChild(sub);
      }

      if (toastTimer) clearTimeout(toastTimer);
      const ttl = kind === "working" ? 25000 : 5500;
      toastTimer = setTimeout(() => {
        if (el) el.style.opacity = "0";
        if (kind !== "working") recentSteps.length = 0;
      }, ttl);
    } catch (err) {}
  };

  window.__baHasChatRoot = () => Boolean(chatRoot());
  window.__baDrainChat = () => {
    scan();
    chatNodes();
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

  function startRootObserver() {
    scan();
    const root = document.documentElement || document.body;
    if (root && root.nodeType === 1) {
      new MutationObserver(scan).observe(root, {childList: true, subtree: true});
    }
  }

  if (document.readyState === "loading" || !document.documentElement) {
    document.addEventListener("DOMContentLoaded", startRootObserver, {once: true});
  } else {
    startRootObserver();
  }
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
  if (Proto && !Proto.__baVideoOff && window.location.hostname !== "meet.google.com") {
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
          const isMeet =
            typeof window !== "undefined" &&
            window.location &&
            window.location.hostname === "meet.google.com";
          stripping = true;
          try {
            desc.set.call(
              this,
              audio.length ? new MediaStream(audio) : isMeet ? new MediaStream() : null
            );
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
