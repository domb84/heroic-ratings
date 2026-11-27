"use strict";
// Heroic Ratings: Talking to the backend (with this run's token), status polling, battery, toasts.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
// ---------------------------------------------------------------- data
// The backend only answers API calls carrying this run's token (from the URL fragment).
const API_TOKEN = new URLSearchParams(location.hash.slice(1)).get("t") || "";
async function api(path, body) {
  const headers = { "X-Heroic-Ratings-Token": API_TOKEN };
  const r = await fetch(path, body === undefined ? { headers }
    : { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

async function loadGames() {
  const d = await api("/api/games");
  if (!Array.isArray(d.games)) {   // refused (e.g. opened without this run's token): say so
    showProgress({ phase: "error", message: "This page can't talk to the app. Open it from Heroic Ratings itself." });
    return;
  }
  const keep = S.view[S.sel]?.id;
  S.games = d.games;
  S.steamDeck = !!d.steam_deck;
  document.body.classList.toggle("lengths", !!d.lengths);   // length column only with an IGDB key
  if (!layoutChecked) { layoutChecked = true; if (d.desktop_layout) showLayoutWarn(true); }
  S.version = d.version;
  showProgress(d.progress);
  buildView(keep);
}

function showProgress(p) {
  S.progress = p || {};
  const busy = p && p.phase in PHASE_TEXT;
  const err = p && p.phase === "error";
  $("progress").hidden = !(busy || err);
  if (busy) {
    const pct = p.total ? (100 * p.done / p.total) : 5;
    $("progressBar").style.width = pct + "%";
    $("progressText").textContent = `${PHASE_TEXT[p.phase]}${p.total ? ` ${p.done}/${p.total}` : ""}${(p.phase === "matching" || p.phase === "wikidata") && p.message ? " — " + p.message : ""}`;
  } else if (err) {
    $("progressBar").style.width = "0";
    $("progressText").textContent = "⚠ " + (p.message || "Error") + " (Menu → Refresh to retry)";
  }
}

let lastFull = 0;
async function poll() {
  try {
    const st = await api("/api/status");
    const busy = st.progress.phase in PHASE_TEXT;
    showProgress(st.progress);
    showBattery(st.battery);
    showUpdate(st.update);
    showHeroicWarn(st.heroic);
    // While syncing, refresh the list every ~6 s so ratings appear as they arrive.
    if (st.version !== S.version && (!busy || Date.now() - lastFull > 6000)) {
      lastFull = Date.now();
      await loadGames();
    }
    setTimeout(poll, busy ? 1500 : 5000);
  } catch (e) {
    setTimeout(poll, 5000);
  }
}

// The installed Heroic is one this version can't drive (yet): say so up front, rather
// than letting an install fail halfway.
function heroicStatusText(h) {
  return { ok: "supported", preliminary: "preliminary support", untested: "newer than tested", unknown: "version unknown",
           unsupported: "not supported yet", "api-changed": "installing off" }[h.status] || h.status;
}
function showHeroicWarn(h) {
  const el = $("heroicWarn");
  const bad = h && !h.installs;
  el.hidden = !bad;
  if (!bad) return;
  const v = esc(h.version || "");
  el.innerHTML = h.status === "unsupported"
    ? `<b>Heroic ${v} isn't supported yet</b> (this version of Heroic Ratings works with ${esc(h.adapter)}). Browsing and launching still work; installing and uninstalling are off until Heroic Ratings is updated for it.`
    : `<b>Heroic ${v} has changed in a way this app doesn't expect</b>, so installing and uninstalling are off. Browsing and launching still work. <span class="note">(Missing: ${esc((h.missing || []).join(", "))})</span>`;
}

// A newer release is out: a small chip in the header; Settings says where to get it.
let updateAnnounced = "";
function showUpdate(u) {
  S.update = u || null;
  const el = $("updateChip");
  el.hidden = !u;
  if (!u) return;
  const ins = u.install || {};
  el.textContent = ins.phase === "downloading" ? `Updating… ${ins.total ? Math.floor(100 * ins.done / ins.total) + "%" : ""}`
    : ins.phase === "done" ? "Restart to update" : `Update ${u.version} available`;
  if ((ins.phase === "done" || ins.phase === "error") && updateAnnounced !== ins.phase + u.version) {
    updateAnnounced = ins.phase + u.version;
    if (ins.phase === "error") toast(`Update failed: ${ins.error}. The current version is unchanged.`, 8000);
    else if (!S.modal) openModal({ kind: "confirm", yes: "Quit now", onYes: quit,
      html: `Updated to <b>${esc(u.version)}</b>.<div class="note">Quit, then start Heroic Ratings again (from Steam in Game Mode) to use it.</div>` });
  }
}

// Battery level in the header (from the Deck's battery; hidden on machines without one).
function showBattery(b) {
  const el = $("battery");
  el.hidden = !b;
  if (!b) return;
  $("batteryText").textContent = `${b.charging ? "⚡" : ""}${b.percent}%`;
  $("batteryFill").style.width = `${b.percent}%`;
  el.classList.toggle("low", b.percent <= 20 && !b.charging);
  el.classList.toggle("charging", b.charging);
  el.title = b.status || "";
}

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(msg, ms = 2500) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

