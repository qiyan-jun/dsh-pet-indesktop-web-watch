/* popup.js —— 弹窗：连接状态、发送计数（读到了多少 / 何时读的 / 送达没有）、本网站暂停、手动看一眼。 */
"use strict";

const $ = (id) => document.getElementById(id);

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "").toLowerCase();
  } catch (error) {
    return "";
  }
}

async function currentTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs && tabs[0] ? tabs[0] : null;
}

function renderStatus(status) {
  $("status").textContent = (status && status.detail) || "还没发过事件";
}

function renderStats(stats) {
  const s = Object.assign({ sent: 0, ok: 0, fail: 0, lastKind: "", lastAt: 0, lastStatus: 0, lastError: "" }, stats || {});
  if (!s.sent && !s.lastAt) {
    $("stats").textContent = "本扩展还没有发送过任何事件。\n可打开本页控制台看采集日志，或到扩展页点「Service Worker」看发送日志。";
    return;
  }
  const when = s.lastAt ? new Date(s.lastAt).toLocaleTimeString() : "—";
  const lines = [
    "已发送 " + s.sent + " 条 · 送达 " + s.ok + " 条 · 失败 " + s.fail + " 条",
    "最近一条：" + (s.lastKind || "—") + " @ " + when + "（HTTP " + (s.lastStatus || "—") + "）",
  ];
  if (s.lastError === "bad_token") lines.push("失败原因：令牌不匹配 → 回桌宠设置页「复制令牌」重新粘贴到扩展选项");
  else if (s.lastError === "offline") lines.push("失败原因：连不上桌宠 → 端口不一致或桌宠没运行（扩展会自动扫端口，扫不到就是桌宠侧没开）");
  else if (s.lastError) lines.push("失败原因：" + s.lastError);
  $("stats").textContent = lines.join("\n");
}

// 端口自愈提示：桌宠被改到别的端口时，直接告诉用户去改哪里
function showResolvedPort() {
  chrome.storage.local.get({ port: 8765, resolvedPort: 0, resolvedName: "" }, (values) => {
    if (!values.resolvedPort) return;
    if (Number(values.resolvedPort) === Number(values.port)) return;
    $("tokenWarn").style.display = "block";
    $("tokenWarn").innerHTML =
      "<b>⚠️ 桌宠在端口 " + values.resolvedPort + (values.resolvedName ? "（" + values.resolvedName + "）" : "") + "</b><br>" +
      "与你这里填的 " + values.port + " 不一致；我已经自动改用 " + values.resolvedPort + "。建议把端口改成 " + values.resolvedPort + " 并保存。";
  });
}

// 没填令牌时给一条醒目提示：这是"扩展装了但桌宠毫无反应"的头号原因
function warnIfNoToken() {
  chrome.storage.local.get({ token: "", port: 8765 }, (values) => {
    if (String(values.token || "").trim()) {
      $("tokenWarn").style.display = "none";
      return;
    }
    $("tokenWarn").style.display = "block";
    $("tokenWarn").innerHTML =
      "<b>⚠️ 还没填令牌（端口 " + (values.port || 8765) + "）</b><br>" +
      "点下面的「打开完整设置」，把桌宠设置页「网页互动 → 复制令牌」的内容粘进「访问令牌」，保存后点「测试连接」。";
  });
}

chrome.runtime.sendMessage({ type: "webwatch:status" }, (status) => renderStatus(status));
chrome.storage.local.get({ stats: {} }, (values) => renderStats(values.stats));
warnIfNoToken();
showResolvedPort();

(async () => {
  const tab = await currentTab();
  const domain = tab ? domainOf(tab.url || "") : "";
  const values = await new Promise((resolve) => chrome.storage.local.get({ pausedDomains: [] }, resolve));
  const paused = (values.pausedDomains || []).indexOf(domain) >= 0;
  $("pauseSite").checked = paused;
  $("pauseSite").disabled = !domain;

  // 关键自检：本页的内容脚本到底有没有在跑？
  // Edge/Chrome 对新加载的解压扩展默认「站点访问权限 = 按点击」，此时脚本不会自动注入，
  // 表现就是"桌宠完全没反应"，而用户无从得知。这里主动问一句，问不到就明确告知怎么改。
  if (tab && tab.id !== undefined && /^https?:/i.test(tab.url || "")) {
    chrome.tabs.sendMessage(tab.id, { type: "webwatch:snapshot" }, (reply) => {
      if (chrome.runtime.lastError || !reply) {
        $("status").innerHTML =
          "<b>⚠️ 本页没有注入内容脚本</b><br>到 <code>edge://extensions</code> → 本扩展 → 把「站点访问权限」改成 <b>在所有网站上</b>，再刷新本页。";
        return;
      }
      $("status").innerHTML =
        "✅ 本页脚本在跑（" + reply.host + "，读到正文 " + reply.textLen + " 字）" +
        (reply.blocked ? "<br>⚠️ " + reply.blocked : "") +
        "<br>已让桌宠看一眼这页；再看下面计数是否 +1。";
      setTimeout(() => chrome.storage.local.get({ stats: {} }, (v) => renderStats(v.stats)), 1200);
    });
  }

  $("pauseSite").addEventListener("change", async () => {
    const list = new Set(values.pausedDomains || []);
    if ($("pauseSite").checked) list.add(domain);
    else list.delete(domain);
    values.pausedDomains = Array.from(list);
    await new Promise((resolve) => chrome.storage.local.set({ pausedDomains: values.pausedDomains }, resolve));
  });

  $("snapshot").addEventListener("click", () => {
    if (tab && tab.id !== undefined) {
      chrome.tabs.sendMessage(tab.id, { type: "webwatch:snapshot" }, (reply) => {
        if (chrome.runtime.lastError || !reply) {
          $("status").innerHTML =
            "<b>⚠️ 本页没有注入内容脚本</b><br>到 <code>edge://extensions</code> → 本扩展 → 「站点访问权限」改成 <b>在所有网站上</b>，再刷新本页。";
          return;
        }
        $("status").textContent = "已让桌宠看一眼（正文 " + reply.textLen + " 字）";
        setTimeout(() => chrome.storage.local.get({ stats: {} }, (v) => renderStats(v.stats)), 1200);
      });
    }
  });
})();

$("options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});
