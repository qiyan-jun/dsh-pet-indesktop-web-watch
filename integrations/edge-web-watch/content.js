/* content.js —— 页面侧采集：标题 / 正文摘要 / 小标题 / 划词 / 视频进度。
 *
 * 隐私红线（与宿主侧 pet/web_watch/protocol.py 的约定一致）：
 * - 只读 document 的可见文本，**绝不读任何表单控件的值**（没有 input.value / textarea.value 的读取路径）；
 * - 页面含密码框（登录/支付）时整体停发，宁可漏掉一次互动；
 * - 查询串由宿主侧统一丢弃，这里不做二次加工；
 * - 只在用户开启且本域名未被暂停时发送。
 */
(() => {
  "use strict";

  const DEFAULTS = {
    enabled: true,
    sendSelection: true,
    sendVideo: true,
    pausedDomains: [],
  };
  const MAX_TEXT = 6000; // 与宿主侧 protocol.MAX_TEXT_CHARS 对齐
  const MAX_SELECTION = 800;
  const MAX_HEADINGS = 8;
  const UPDATE_DEBOUNCE_MS = 3000;
  const VIDEO_TICK_MS = 12000;
  const VIDEO_POLL_MS = 5000;

  const MAIN_SELECTORS = [
    "article",
    "main",
    "[role=main]",
    "#js_content",
    ".markdown-body",
    ".article-content",
    ".post-content",
  ];

  let settings = Object.assign({}, DEFAULTS);
  let started = false;
  let lastPageKey = "";
  let lastSelection = "";
  let lastVideoSentAt = 0;
  let updateTimer = null;

  const domain = (location.hostname || "").replace(/^www\./, "").toLowerCase();

  function isPaused() {
    return (settings.pausedDomains || []).some(
      (rule) => typeof rule === "string" && rule.trim() && (domain === rule.trim().toLowerCase() || domain.endsWith("." + rule.trim().toLowerCase()))
    );
  }

  // 凭据页一律不读：登录框、支付页、改密码页都可能有 password 输入
  function hasPasswordField() {
    try {
      return !!document.querySelector('input[type="password"]');
    } catch (e) {
      return false;
    }
  }

  function blocked() {
    return !settings.enabled || isPaused() || hasPasswordField();
  }

  function pickMainNode() {
    for (const selector of MAIN_SELECTORS) {
      const node = document.querySelector(selector);
      if (node && (node.innerText || "").trim().length > 200) return node;
    }
    return document.body || null;
  }

  function mainText() {
    const node = pickMainNode();
    if (!node) return "";
    return (node.innerText || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
  }

  function headings() {
    const out = [];
    for (const node of document.querySelectorAll("h1, h2, h3")) {
      const text = (node.innerText || "").replace(/\s+/g, " ").trim();
      if (text) out.push(text.slice(0, 120));
      if (out.length >= MAX_HEADINGS) break;
    }
    return out;
  }

  function videoInfo() {
    const video = document.querySelector("video");
    if (!video || !isFinite(video.duration) || video.duration <= 0) return null;
    const meta = document.querySelector('meta[property="og:title"]');
    const title = (meta && meta.content) || document.title || "";
    return {
      title: String(title).slice(0, 300),
      position: Number(video.currentTime) || 0,
      duration: Number(video.duration) || 0,
      paused: !!video.paused,
    };
  }

  function send(kind, extra) {
    if (blocked()) return;
    const payload = Object.assign(
      { kind: kind, url: location.href, title: document.title || "", ts: Date.now() / 1000 },
      extra || {}
    );
    try {
      chrome.runtime.sendMessage({ type: "webwatch:event", payload: payload });
    } catch (e) {
      /* 扩展被重载时 runtime 可能短暂不可用：静默丢弃这一次 */
    }
  }

  function sendPage(kind) {
    if (blocked()) return;
    const text = mainText();
    const key = kind + "|" + location.href + "|" + document.title + "|" + text.length + "|" + text.slice(0, 160);
    if (kind === "page_update" && key === lastPageKey) return;
    lastPageKey = key;
    send(kind, { text: text, headings: headings() });
  }

  function checkSelection() {
    if (blocked() || !settings.sendSelection) return;
    const selection = String(window.getSelection() || "").replace(/\s+/g, " ").trim().slice(0, MAX_SELECTION);
    if (selection.length < 2 || selection === lastSelection) return;
    lastSelection = selection;
    send("selection", { selection: selection, text: mainText(), headings: headings() });
  }

  function pollVideo() {
    if (blocked() || !settings.sendVideo) return;
    const info = videoInfo();
    if (!info) return;
    const now = Date.now();
    if (now - lastVideoSentAt < VIDEO_TICK_MS) return;
    lastVideoSentAt = now;
    send("video", {
      video_title: info.title,
      video_position: info.position,
      video_duration: info.duration,
      video_paused: info.paused,
      text: mainText(),
      headings: headings(),
    });
  }

  function start() {
    if (started) return;
    started = true;
    sendPage("page_open");

    try {
      const observer = new MutationObserver(() => {
        if (updateTimer) clearTimeout(updateTimer);
        updateTimer = setTimeout(() => sendPage("page_update"), UPDATE_DEBOUNCE_MS);
      });
      observer.observe(document.documentElement || document, { childList: true, subtree: true, characterData: true });
    } catch (e) {
      /* 观察器不可用时退化为"只在打开页面时说一次" */
    }

    document.addEventListener("mouseup", () => setTimeout(checkSelection, 120), true);
    document.addEventListener("keyup", (event) => {
      if (event.key === "Shift" || event.shiftKey) setTimeout(checkSelection, 150);
    });

    setInterval(pollVideo, VIDEO_POLL_MS);
    window.addEventListener("pagehide", () => send("page_leave"));
  }

  chrome.storage.local.get(DEFAULTS, (values) => {
    settings = Object.assign({}, DEFAULTS, values || {});
    start();
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    for (const key of Object.keys(changes)) settings[key] = changes[key].newValue;
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === "webwatch:snapshot") sendPage("page_open");
  });
})();
