/* popup.js —— 弹窗：连接状态、本网站暂停、手动让桌宠看一眼当前页。 */
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
  const text = (status && status.detail) || "还没发过事件";
  $("status").textContent = text;
}

chrome.runtime.sendMessage({ type: "webwatch:status" }, (status) => renderStatus(status));

(async () => {
  const tab = await currentTab();
  const domain = tab ? domainOf(tab.url || "") : "";
  const values = await new Promise((resolve) => chrome.storage.local.get({ pausedDomains: [] }, resolve));
  const paused = (values.pausedDomains || []).indexOf(domain) >= 0;
  $("pauseSite").checked = paused;
  $("pauseSite").disabled = !domain;

  $("pauseSite").addEventListener("change", async () => {
    const list = new Set(values.pausedDomains || []);
    if ($("pauseSite").checked) list.add(domain);
    else list.delete(domain);
    values.pausedDomains = Array.from(list);
    await new Promise((resolve) => chrome.storage.local.set({ pausedDomains: values.pausedDomains }, resolve));
  });

  $("snapshot").addEventListener("click", () => {
    if (tab && tab.id !== undefined) {
      chrome.tabs.sendMessage(tab.id, { type: "webwatch:snapshot" }, () => {
        $("status").textContent = "已请求桌宠看一眼这页…";
      });
    }
  });
})();

$("options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});
