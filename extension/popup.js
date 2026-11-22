const $ = (s) => document.querySelector(s);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function showErr(msg) {
  const el = $("#err");
  if (!msg) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = msg;
}

async function msg(payload) {
  try {
    return await chrome.runtime.sendMessage(payload);
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function refresh() {
  const h = await msg({ type: "health" });
  const dot = $("#dot");
  if (h.ok && h.health) {
    dot.className = "dot ok";
    const c = h.health.counts || {};
    const act = h.health.active ? ` · ${h.health.active} active` : "";
    $("#stats").innerHTML = `
      <span>worker <b>${h.health.worker_running ? "on" : "off"}</b>${act}</span>
      <span>pending <b>${c.pending || 0}</b></span>
      <span>done <b>${c.done || 0}</b></span>
      <span>failed <b>${c.failed || 0}</b></span>`;
  } else {
    dot.className = "dot err";
    $("#stats").innerHTML = `<span style="color:var(--err)">Server unreachable — is <b>yt2jellyfin serve</b> running?</span>`;
  }

  const j = await msg({ type: "jobs" });
  const box = $("#jobs");
  if (!j.ok || !j.jobs || !j.jobs.length) {
    box.innerHTML = '<div class="empty">No jobs</div>';
    return;
  }
  box.innerHTML = j.jobs.slice(0, 8).map((job) => `
    <div class="job">
      <span class="status ${esc(job.status)}">${esc(job.status)}</span>
      <div class="u">${esc(job.title || job.url)}</div>
      <div class="m">${job.status === "running" ? esc(job.message || "") : esc(job.error || job.url)}</div>
    </div>`).join("");
}

async function currentTabUrl() {
  // Ask the content script (avoids tabs permission).
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return null;
  try {
    const res = await chrome.tabs.sendMessage(tab.id, { type: "ping" });
    return res && res.url ? res.url : tab.url || null;
  } catch {
    return tab.url || null;
  }
}

$("#btn-tab").addEventListener("click", async () => {
  const url = await currentTabUrl();
  if (url && /youtube\.com|youtu\.be/.test(url)) $("#url").value = url.split("#")[0];
  else showErr("No YouTube URL found on this tab.");
});

$("#btn-send").addEventListener("click", async () => {
  const url = $("#url").value.trim();
  showErr("");
  if (!/^https?:\/\//i.test(url)) {
    showErr("Enter a valid URL first.");
    return;
  }
  const res = await msg({ type: "download", url });
  if (res.ok) {
    $("#url").value = "";
    refresh();
  } else {
    showErr(res.error || "Failed");
  }
});

$("#url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#btn-send").click();
});

$("#btn-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
$("#btn-web").addEventListener("click", async () => {
  await msg({ type: "open-webui" });
});

refresh();
setInterval(refresh, 3000);
