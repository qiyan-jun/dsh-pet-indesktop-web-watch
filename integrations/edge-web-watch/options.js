/* options.js —— 选项页：端口 / 令牌 / 开关 / 域名暂停名单 + 连接自检。 */
"use strict";

const DEFAULTS = {
  port: 8765,
  token: "",
  enabled: true,
  sendSelection: true,
  sendVideo: true,
  pausedDomains: [],
};

const $ = (id) => document.getElementById(id);

function setStatus(text, ok) {
  const node = $("status");
  node.textContent = text;
  node.className = ok === true ? "ok" : ok === false ? "bad" : "";
}

function readForm() {
  return {
    enabled: $("enabled").checked,
    port: Math.max(1, Math.min(65535, parseInt($("port").value, 10) || DEFAULTS.port)),
    token: $("token").value.trim(),
    sendSelection: $("sendSelection").checked,
    sendVideo: $("sendVideo").checked,
    pausedDomains: $("pausedDomains")
      .value.split("\n")
      .map((line) => line.trim().toLowerCase())
      .filter(Boolean),
  };
}

function fillForm(values) {
  $("enabled").checked = !!values.enabled;
  $("port").value = values.port;
  $("token").value = values.token || "";
  $("sendSelection").checked = !!values.sendSelection;
  $("sendVideo").checked = !!values.sendVideo;
  $("pausedDomains").value = (values.pausedDomains || []).join("\n");
}

async function test(values) {
  const url = "http://127.0.0.1:" + values.port + "/ping";
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Pet-Token": values.token },
      body: "{}",
    });
    if (response.status === 200) {
      const body = await response.json().catch(() => ({}));
      setStatus("连接正常" + (body && body.name ? "，桌宠：" + body.name : ""), true);
      return true;
    }
    if (response.status === 401) {
      setStatus("令牌不匹配：请回到桌宠设置页重新复制", false);
      return false;
    }
    setStatus("桌宠返回 HTTP " + response.status, false);
    return false;
  } catch (error) {
    setStatus("连不上本机桌宠：确认桌宠在运行，且端口一致", false);
    return false;
  }
}

/* 自检：把"为什么连不上"用扩展自己能拿到的事实说清楚，不用开 DevTools。
 *
 * 背景：Edge/Chrome 对解压扩展默认「站点访问权限 = 按点击」，此时 host_permissions
 * 处于**被扣留**状态 —— 内容脚本不注入，扩展页里对 127.0.0.1 的 fetch 也会被直接
 * 拒绝（表现为 fetch 抛错、桌宠侧零连接、零日志）。`chrome.permissions.contains`
 * 能直接问出这个状态，比让用户读控制台靠谱。 */
async function selfCheck(values) {
  const lines = [];
  lines.push("正在探测端口：" + values.port + "（必须与桌宠设置页「本地接收端口」一致）");
  const check = (origin) =>
    new Promise((resolve) => {
      try {
        chrome.permissions.contains({ origins: [origin] }, (ok) => resolve(!!ok && !chrome.runtime.lastError));
      } catch (e) {
        resolve(false);
      }
    });
  const granted127 = await check("http://127.0.0.1/*");
  const grantedLocal = await check("http://localhost/*");
  const lnaGranted = await new Promise((resolve) => {
    try {
      chrome.permissions.contains({ permissions: ["localNetwork"] }, (ok) => resolve(!!ok && !chrome.runtime.lastError));
    } catch (e) {
      resolve(false);
    }
  });
  lines.push(
    "主机权限：127.0.0.1 " + (granted127 ? "已授予 ✅" : "被扣留 ❌") +
    " · localhost " + (grantedLocal ? "已授予 ✅" : "未授予 ❌")
  );
  if (lnaGranted) lines.push("（本地网络权限也已被授予）");

  const probe = async (host) => {
    const url = "http://" + host + ":" + values.port + "/health";
    try {
      const r = await fetch(url, { method: "GET", cache: "no-store" });
      return host + " → HTTP " + r.status + (r.status === 200 ? " ✅" : "");
    } catch (e) {
      return host + " → fetch 失败（" + ((e && (e.name || e.message)) || "unknown") + "）";
    }
  };
  // 生产路径优先：真正发事件的是 Service Worker，不是本选项页。
  const swResult = await new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: "webwatch:ping" }, (reply) => {
        if (chrome.runtime.lastError || !reply) {
          resolve("Service Worker 未能应答（" + (chrome.runtime.lastError && chrome.runtime.lastError.message) + "）");
          return;
        }
        resolve(
          "Service Worker → " + (reply.ok ? "HTTP " + reply.status + " ✅ 生产路径可用" : "失败（" + reply.detail + "，HTTP " + reply.status + "）")
        );
      });
    } catch (e) {
      resolve("Service Worker 通道异常：" + e);
    }
  });
  lines.push(swResult);

  // 端口扫描：桌宠有可能被设置在别的端口（设置页独立进程保存会把端口写回旧值），
  // 所以直接把"我找到了谁"报出来，而不是让用户比对两个界面上的数字。
  const scan = await new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ type: "webwatch:scan" }, (reply) => resolve(reply || { found: [] }));
    } catch (e) {
      resolve({ found: [] });
    }
  });
  if (scan.found && scan.found.length) {
    lines.push(
      "扫描结果：桌宠在端口 " + scan.found.map((f) => f.port + (f.name ? "(" + f.name + ")" : "")).join("、") +
      (scan.found[0].port !== Number(values.port) ? " ⚠️ 与上面填写的不一致 → 请把端口改成 " + scan.found[0].port + " 并保存" : " ✅ 与填写一致")
    );
  } else {
    lines.push("扫描结果：候选端口（8765/8755/8775/8888/9000/8000/9527）上都没有响应 → 桌宠没运行，或「启用网页互动」没保存");
  }

  lines.push(await probe("127.0.0.1"));
  lines.push(await probe("localhost"));

  if (!granted127) {
    lines.push("结论：主权限被扣留 → 到 edge://extensions → 本扩展 → 「站点访问权限」选「在所有网站上」，再点本页右上角的重载图标，并刷新要测试的网页。");
  } else if (swResult.indexOf("✅") < 0) {
    lines.push(
      "结论：主机权限没问题，但请求没到桌宠。按这个顺序查：\n" +
      "① 端口是否和桌宠设置页「本地接收端口」完全一致（上面第一行打印的就是本扩展在用的端口）；\n" +
      "② 桌宠是否在运行、且「启用网页互动」已保存；\n" +
      "③ 桌宠是否刚改过端口（改完要同步改这里并保存）。"
    );
  } else {
    lines.push("结论：生产路径可用（Service Worker 能连上桌宠）。若弹窗仍显示未发送，请刷新页面让内容脚本重新注入。");
  }
  $("diag").textContent = lines.join("\n");
  return granted127;
}

chrome.storage.local.get(DEFAULTS, (values) => fillForm(Object.assign({}, DEFAULTS, values || {})));

$("save").addEventListener("click", () => {
  const values = readForm();
  chrome.storage.local.set(values, () => setStatus("已保存", true));
});

$("test").addEventListener("click", () => test(readForm()));

$("diagBtn").addEventListener("click", () => {
  setStatus("自检中…");
  selfCheck(readForm());
});
