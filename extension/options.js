const $ = (s) => document.querySelector(s);
const DEFAULTS = { server: "http://127.0.0.1:8392", dest: "" };

function normalizeServer(v) {
  v = (v || "").trim().replace(/\/+$/, "");
  if (v && !/^https?:\/\//i.test(v)) v = "http://" + v;
  return v || DEFAULTS.server;
}

async function load() {
  const cfg = await chrome.storage.sync.get(DEFAULTS);
  $("#server").value = cfg.server;
  $("#dest").value = cfg.dest || "";
}

function status(msg, ok = true) {
  const el = $("#status");
  el.textContent = msg;
  el.style.color = ok ? "var(--ok)" : "var(--err)";
}

$("#save").addEventListener("click", async () => {
  const server = normalizeServer($("#server").value);
  const dest = $("#dest").value.trim();
  await chrome.storage.sync.set({ server, dest });
  $("#server").value = server;
  status("Saved ✓");
});

$("#test").addEventListener("click", async () => {
  const server = normalizeServer($("#server").value);
  try {
    const res = await fetch(server + "/api/health", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const h = await res.json();
    status(h.ok ? "Connected ✓" : "Unexpected response", !!h.ok);
  } catch (e) {
    status("Failed: " + e.message + " — is yt2jellyfin serve running?", false);
  }
});

load();
