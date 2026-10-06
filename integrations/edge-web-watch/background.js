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

/* 端口自愈用的候选清单。
 *
 * 实测教训（2026-10-06）：桌宠设置页是**独立进程**，它保存时会把"打开那一刻"的
 * 配置整份写回——端口被改回旧值（8755），而扩展里还写着 8765，于是表现为
 * "突然又连不上"。与其让用户去比对两个界面上的数字，不如让扩展自己认路：
 * 配置端口优先，连不上就在候选清单里找带桌宠签名的 /health。 */
const CANDIDATE_PORTS = [8765, 8755, 8775, 8888, 9000, 8000, 9527];
const PORT_CACHE_MS = 60000;

function storageGet(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

function storageSet(values) {
  return new Promise((resolve) => chrome.storage.local.set(values, resolve));
}

/** 探测某端口是不是桌宠：/health 返回 {"ok":true,"name":...} 才算（免令牌）。 */
async function probeHealth(port) {
  try {
    const response = await fetch("http://127.0.0.1:" + Number(port) + "/health", {
      method: "GET",
      cache: "no-store",
    });
    if (response.status !== 200) return null;
    const body = await response.json().catch(() => null);
    return body && body.ok === true ? body : null;
  } catch (error) {
    return null;
  }
}

/** 找到桌宠实际监听的端口（缓存 60s；force=true 时重扫）。 */
async function resolvePort(configuredPort, options) {
  const force = !!(options && options.force);
  const fallback = Number(configuredPort) || DEFAULTS.port;
  const cached = await storageGet({ resolvedPort: 0, resolvedAt: 0, resolvedName: "", foundPorts: [] });
  if (!force && cached.resolvedPort && Date.now() - Number(cached.resolvedAt || 0) < PORT_CACHE_MS) {
    return { port: Number(cached.resolvedPort), name: cached.resolvedName || "", found: cached.foundPorts || [] };
  }
  const order = [fallback].concat(CANDIDATE_PORTS.filter((p) => p !== fallback));
  const found = [];
  for (const port of order) {
    const body = await probeHealth(port);
    if (body) found.push({ port: port, name: body.name || "" });
  }
  if (!found.length) {
    await storageSet({ resolvedPort: 0, resolvedAt: Date.now(), resolvedName: "", foundPorts: [] });
    return { port: fallback, name: "", found: [] };
  }
  await storageSet({
    resolvedPort: found[0].port,
    resolvedAt: Date.now(),
    resolvedName: found[0].name,
    foundPorts: found.map((item) => item.port),
  });
  if (found[0].port !== fallback) {
    console.warn(
      "[桌宠网页互动] 配置端口 " + fallback + " 上没有桌宠，改在 " + found[0].port +
      " 找到（候选：" + found.map((i) => i.port).join("/") + "）。建议把扩展选项的端口改成 " + found[0].port + "。"
    );
  }
  return { port: found[0].port, name: found[0].name, found: found };
}

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

//: 计数与最近一条的痕迹存进 storage.local：弹窗不用等 SW 醒来也能读到历史
function bumpCounters(patch) {
  chrome.storage.local.get({ stats: {} }, (values) => {
    const stats = Object.assign(
      { sent: 0, ok: 0, fail: 0, lastKind: "", lastAt: 0, lastStatus: 0, lastError: "" },
      values.stats || {}
    );
    if (patch.sent) stats.sent += patch.sent;
    if (patch.ok) stats.ok += patch.ok;
    if (patch.fail) stats.fail += patch.fail;
    if (patch.lastKind !== undefined) stats.lastKind = patch.lastKind;
    if (patch.lastAt !== undefined) stats.lastAt = patch.lastAt;
    if (patch.lastStatus !== undefined) stats.lastStatus = patch.lastStatus;
    if (patch.lastError !== undefined) stats.lastError = patch.lastError;
    chrome.storage.local.set({ stats: stats });
  });
}

function setStatus(state, detail, ok) {
  lastStatus = { state: state, detail: detail, at: Date.now() };
  setBadge(ok);
  // Service Worker 控制台可见（edge://extensions → 该扩展 → Service Worker）
  console.info("[桌宠网页互动] " + state + " · " + detail);
}

async function post(path, payload, port) {
  const settings = await loadSettings();
  const target = Number(port) || Number(settings.port) || DEFAULTS.port;
  const url = "http://127.0.0.1:" + target + path;
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
      const settings = await loadSettings();
      let resolved = await resolvePort(settings.port);
      let port = resolved.port;
      let status = 0;
      console.info("[桌宠网页互动] 发送 " + payload.kind + " → 127.0.0.1:" + port);
      try {
        status = await post("/event", payload, port);
      } catch (error) {
        // 连接失败：强制重扫（用户可能刚在设置页改了端口），换到新端口就重试这一条
        resolved = await resolvePort(settings.port, { force: true });
        if (resolved.port && resolved.port !== port) {
          port = resolved.port;
          console.info("[桌宠网页互动] 换到端口 " + port + " 重试");
          try {
            status = await post("/event", payload, port);
          } catch (retryError) {
            queue = [];
            setStatus("offline", "连不上本机桌宠（已扫描 " + CANDIDATE_PORTS.join("/") + "，都没有响应）", false);
            bumpCounters({ fail: 1, lastKind: payload.kind, lastAt: Date.now(), lastStatus: 0, lastError: "offline" });
            break;
          }
        } else {
          queue = [];
          setStatus("offline", "连不上本机桌宠（检查桌宠是否在运行、「启用网页互动」是否已保存）", false);
          bumpCounters({ fail: 1, lastKind: payload.kind, lastAt: Date.now(), lastStatus: 0, lastError: "offline" });
          break;
        }
      }
      if (status === 204) {
        queue.shift();
        setStatus("ok", "已送达桌宠（端口 " + port + "）", true);
        bumpCounters({ sent: 1, ok: 1, lastKind: payload.kind, lastAt: Date.now(), lastStatus: 204, lastError: "" });
      } else if (status === 401) {
        queue = [];
        setStatus("unauthorized", "令牌不匹配（复制桌宠设置页里的令牌）", false);
        bumpCounters({ sent: 1, fail: 1, lastKind: payload.kind, lastAt: Date.now(), lastStatus: 401, lastError: "bad_token" });
        break;
      } else if (status === 413 || status === 400) {
        queue.shift(); // 这一条内容本身有问题，丢掉，不阻塞后续
        setStatus("rejected", "这条事件被桌宠拒绝（HTTP " + status + "）", false);
        bumpCounters({ sent: 1, fail: 1, lastKind: payload.kind, lastAt: Date.now(), lastStatus: status, lastError: "rejected" });
      } else {
        queue = [];
        setStatus("error", "桌宠返回 HTTP " + status, false);
        bumpCounters({ sent: 1, fail: 1, lastKind: payload.kind, lastAt: Date.now(), lastStatus: status, lastError: "http_" + status });
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
  if (message.type === "webwatch:scan") {
    // 端口扫描：把"桌宠到底在哪个端口"直接告诉用户，省得比对两个界面
    loadSettings()
      .then((settings) => resolvePort(settings.port, { force: true }))
      .then((resolved) => sendResponse({ ok: resolved.found.length > 0, found: resolved.found, port: resolved.port }))
      .catch(() => sendResponse({ ok: false, found: [], port: 0 }));
    return true;
  }
  if (message.type === "webwatch:ping") {
    loadSettings()
      .then((settings) => resolvePort(settings.port).then((resolved) => ({ settings: settings, resolved: resolved })))
      .then(({ settings, resolved }) =>
        post("/ping", {}, resolved.port).then((status) => ({ status: status, port: resolved.port, settings: settings }))
      )
      .then(({ status, port }) => {
        const ok = status === 200;
        setStatus(
          ok ? "ok" : status === 401 ? "unauthorized" : "error",
          ok ? "连接正常（端口 " + port + "）" : "HTTP " + status,
          ok
        );
        sendResponse({ ok: ok, status: status, port: port, detail: lastStatus.detail });
      })
      .catch(() => {
        setStatus("offline", "连不上本机桌宠", false);
        sendResponse({ ok: false, status: 0, port: 0, detail: lastStatus.detail });
      });
    return true; // 异步响应
  }
  return false;
});
