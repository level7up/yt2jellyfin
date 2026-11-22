// yt2jellyfin content script — floating "Send" button on YouTube pages.

(() => {
  if (window.__yt2jInjected) return;
  window.__yt2jInjected = true;

  const btn = document.createElement("button");
  btn.id = "yt2j-btn";
  btn.type = "button";
  btn.title = "Download with yt2jellyfin";

  function label() {
    const path = location.pathname;
    const isPlaylist = path === "/playlist" || location.search.includes("list=");
    const isWatch = path === "/watch";
    if (isPlaylist && !isWatch) return "⬇ Playlist to Jellyfin";
    if (isWatch && location.search.includes("list=")) return "⬇ Video to Jellyfin";
    if (isWatch) return "⬇ Video to Jellyfin";
    if (path.startsWith("/@") || path.startsWith("/channel/") || path.startsWith("/playlist"))
      return "⬇ Send to Jellyfin";
    return "⬇ Jellyfin";
  }

  function refreshLabel() {
    btn.textContent = label();
  }

  let busy = false;
  btn.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    btn.classList.add("yt2j-busy");
    refreshLabel();
    btn.textContent = "Sending…";
    try {
      const url = location.href.split("#")[0];
      const res = await chrome.runtime.sendMessage({ type: "download", url });
      if (res && res.ok) {
        toast("Queued ✓ " + (res.job && res.job.id ? res.job.id : ""), false);
      } else {
        toast("Error: " + ((res && res.error) || "no response"), true);
      }
    } catch (e) {
      toast("Error: " + e.message, true);
    } finally {
      busy = false;
      btn.classList.remove("yt2j-busy");
      refreshLabel();
    }
  });

  function toast(msg, isErr) {
    const t = document.createElement("div");
    t.id = "yt2j-toast";
    t.textContent = msg;
    if (isErr) t.classList.add("yt2j-err");
    document.documentElement.appendChild(t);
    requestAnimationFrame(() => t.classList.add("yt2j-show"));
    setTimeout(() => {
      t.classList.remove("yt2j-show");
      setTimeout(() => t.remove(), 300);
    }, 3200);
  }

  // SPA navigation: YouTube changes URL without reload — keep label fresh.
  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      refreshLabel();
      document.body.appendChild(btn); // re-attach if DOM was replaced
    }
  }, 1000);

  const attach = () => {
    if (!document.body.contains(btn)) document.body.appendChild(btn);
    refreshLabel();
  };
  attach();
  new MutationObserver(attach).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "ping") {
      sendResponse({ url: location.href.split("#")[0] });
      return true;
    }
  });
})();
