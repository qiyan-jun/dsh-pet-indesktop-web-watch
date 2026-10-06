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

chrome.storage.local.get(DEFAULTS, (values) => fillForm(Object.assign({}, DEFAULTS, values || {})));

$("save").addEventListener("click", () => {
  const values = readForm();
  chrome.storage.local.set(values, () => setStatus("已保存", true));
});

$("test").addEventListener("click", () => test(readForm()));
