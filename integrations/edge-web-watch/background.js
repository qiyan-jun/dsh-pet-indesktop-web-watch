/* background.js —— service worker：把内容脚本采集到的事件 POST 给本机桌宠。
 *
 * 设计要点：
 * - 只连 127.0.0.1（manifest 的 host_permissions 仅此一项），不向任何外部服务器发送；
 * - 事件入队后串行发送，最多保留 maxQueue 条：桌宠没开时不堆积、不报错刷屏；
 * - 令牌错误（401）与连不上分别给出可读状态，供弹窗与选项页展示。
 */
"use strict";

const DEFAULTS = {
  port: 8765,
  token: "",
  enabled: true,
  sendSelection: true,
  sendVideo: true,
  maxQueue: 20,
};

let queue = [];
let draining = false;
let lastStatus = { state: "idle", detail: "还没发过事件", at: 0 };

function loadSettings() {
  return new Promise((resolve) => {
    chrome.storage.local.get(DEFAULTS, (values) => resolve(Object.assign({}, DEFAULTS, values || {})));
  });
}

function setBadge(ok) {
  try {
    chrome.action.setBadgeText({ text: ok ? "" : "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#c0392b" });
  } catch (e) {
    /* 无 action 权限时忽略 */
  }
}

function setStatus(state, detail, ok) {
  lastStatus = { state: state, detail: detail, at: Date.now() };
  setBadge(ok);
}

async function post(path, payload) {
  const settings = await loadSettings();
  const url = "http://127.0.0.1:" + Number(settings.port || DEFAULTS.port) + path;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Pet-Token": String(settings.token || "") },
    body: JSON.stringify(payload || {}),
  });
  return response.status;
}

async function drain() {
  if (draining) return;
  draining = true;
  try {
    while (queue.length) {
      const payload = queue[0];
      let status = 0;
      try {
        status = await post("/event", payload);
      } catch (error) {
        // 桌宠没在跑 / 端口不对：整队丢掉，避免无限重试堆积
        queue = [];
        setStatus("offline", "连不上本机桌宠（检查端口与桌宠是否在运行）", false);
        break;
      }
      if (status === 204) {
        queue.shift();
        setStatus("ok", "已送达桌宠", true);
      } else if (status === 401) {
        queue = [];
        setStatus("unauthorized", "令牌不匹配（复制桌宠设置页里的令牌）", false);
        break;
      } else if (status === 413 || status === 400) {
        queue.shift(); // 这一条内容本身有问题，丢掉，不阻塞后续
        setStatus("rejected", "这条事件被桌宠拒绝（HTTP " + status + "）", false);
      } else {
        queue = [];
        setStatus("error", "桌宠返回 HTTP " + status, false);
        break;
      }
    }
  } finally {
    draining = false;
  }
}

function enqueue(payload) {
  if (!payload || typeof payload !== "object") return;
  queue.push(payload);
  while (queue.length > DEFAULTS.maxQueue * 4) queue.shift();
  drain();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message) return false;
  if (message.type === "webwatch:event") {
    enqueue(message.payload);
    return false;
  }
  if (message.type === "webwatch:status") {
    sendResponse(lastStatus);
    return true;
  }
  if (message.type === "webwatch:ping") {
    post("/ping", {})
      .then((status) => {
        const ok = status === 200;
        setStatus(ok ? "ok" : status === 401 ? "unauthorized" : "error", ok ? "连接正常" : "HTTP " + status, ok);
        sendResponse({ ok: ok, status: status, detail: lastStatus.detail });
      })
      .catch(() => {
        setStatus("offline", "连不上本机桌宠", false);
        sendResponse({ ok: false, status: 0, detail: lastStatus.detail });
      });
    return true; // 异步响应
  }
  return false;
});
