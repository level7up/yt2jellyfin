// yt2jellyfin background service worker — talks to the local server.

const DEFAULTS = { server: "http://127.0.0.1:8392", dest: "" };

async function config() {
  return chrome.storage.sync.get(DEFAULTS);
}

function base(server) {
  return String(server || DEFAULTS.server).trim().replace(/\/+$/, "");
}

async function api(path, opts = {}) {
  const { server } = await config();
  const res = await fetch(base(server) + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

async function sendDownload(url, dest) {
  const cfg = await config();
  const body = { url, dest: (dest !== undefined ? dest : cfg.dest) || null };
  return api("/api/jobs", { method: "POST", body: JSON.stringify(body) });
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "yt2j-send",
      title: "Send to yt2jellyfin",
      contexts: ["link", "page", "video"],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info) => {
  const url = info.linkUrl || info.srcUrl || info.pageUrl;
  if (!url || !/^https?:\/\//i.test(url)) return;
  sendDownload(url)
    .then((job) => {
      chrome.action.setBadgeText({ text: "+" });
      chrome.action.setBadgeBackgroundColor({ color: "#3ecf8e" });
      setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
    })
    .catch((err) => {
      chrome.action.setBadgeText({ text: "!" });
      chrome.action.setBadgeBackgroundColor({ color: "#ff6b6b" });
      setTimeout(() => chrome.action.setBadgeText({ text: "" }), 4000);
      console.error("yt2jellyfin:", err);
    });
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || typeof msg.type !== "string") return;
  const handle = async () => {
    switch (msg.type) {
      case "download": {
        const job = await sendDownload(msg.url, msg.dest);
        return { ok: true, job };
      }
      case "health":
        return { ok: true, health: await api("/api/health") };
      case "jobs":
        return { ok: true, jobs: await api("/api/jobs") };
      case "open-webui": {
        const { server } = await config();
        chrome.tabs.create({ url: base(server) + "/" });
        return { ok: true };
      }
      default:
        return { ok: false, error: "unknown message" };
    }
  };
  handle()
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true; // async response
});
