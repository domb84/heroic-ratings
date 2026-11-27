"use strict";
// Heroic Ratings: Launching, installing, uninstalling, the queue and download progress, quitting, search.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
// Quitting stops the background Heroic, and with it any download, uninstall or queued job.
function busyJobs() {
  const ds = Object.values(S.downloads);
  return { installing: ds.filter(d => d.active || d.starting || d.finishing || (d.percent != null)).length,
           uninstalling: ds.filter(d => d.uninstalling).length, queued: ds.filter(d => d.queued).length };
}
function quit() {
  const b = busyJobs();
  if (!b.installing && !b.uninstalling && !b.queued) return reallyQuit();
  const parts = [b.installing && `${b.installing} install${b.installing > 1 ? "s" : ""} in progress`,
                 b.uninstalling && `${b.uninstalling} uninstall${b.uninstalling > 1 ? "s" : ""} in progress`,
                 b.queued && `${b.queued} more waiting in the queue`].filter(Boolean);
  openModal({ kind: "confirm", yes: "Quit anyway", onYes: reallyQuit,
    html: `Heroic is still working: ${parts.join(", ")}.<div class="note">Quitting stops ${b.queued && !b.installing && !b.uninstalling ? "the queue" : "it"}. Downloads Heroic has started continue next time Heroic runs; queued jobs carry on next time you open this app.</div>` });
}
async function reallyQuit() {
  closeModal();
  toast("Closing…");
  try { await api("/api/quit", {}); } catch (e) { /* server gone */ }
  setTimeout(() => window.close(), 300);
}

function setSort(i) {
  S.sortIdx = i;
  S.dir = SORTS[i].dir;
  savePrefs();
  buildView(S.view[S.sel]?.id);
}

function launchSelected() {
  const g = S.view[S.sel];
  if (!g) return;
  if (S.downloads[g.id]?.queued || S.downloads[g.id]?.starting) { toast(`${g.title} is already in the queue`, 3000); return; }
  if (!g.installed && g.managed === false) {   // e.g. Heroic 3's Steam and itch.io games
    openModal({ kind: "confirm", yes: "OK", onYes: () => {},
      html: `<b>${esc(g.title)}</b> isn't installed.<div class="note">Install ${esc(g.store)} games in Heroic itself for now; this app can't install them yet.</div>` });
    return;
  }
  if (!g.installed) {
    openModal({
      kind: "confirm", yes: "Install",
      html: `<b>${esc(g.title)}</b> isn't installed.<br>Install it with Heroic?
        <div class="note">Next you choose the install options (location, DLC, Wine/Proton, …). Heroic does the download.</div>`,
      onYes: () => installViaHeroic(g),
    });
    return;
  }
  openModal({
    kind: "confirm", yes: "Launch", html: `Launch <b>${esc(g.title)}</b>?`,
    onYes: async () => {
      // Only through the game's own Steam shortcut, so it runs as its own Steam app
      // and keeps running when this app closes. Refused (with the reason) otherwise.
      pauseFor(`Playing ${g.title}`, "Starting through Steam…");
      let r;
      try { r = await api("/api/launch", { id: g.id }); } catch { r = { error: "network" }; }
      if (r.ok) return;
      resume(true);
      heroicProblem(r.error, `launch <b>${esc(g.title)}</b>`, "", g);
    },
  });
}

// Installing: Heroic's own "install?" prompt is broken (Heroic issue #6053), so the
// app reads the same options Heroic's install dialog offers (from a hidden Heroic),
// shows them here, and queues the install with Heroic's own install function.
const fmtSize = b => !b ? "?" : b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1048576)) + " MB";
const langName = (() => {
  let dn = null;
  try { dn = new Intl.DisplayNames(["en"], { type: "language" }); } catch {}
  return code => { try { return dn ? dn.of(code) || code : code; } catch { return code; } };
})();

async function installViaHeroic(g, platform) {
  pauseFor(`Getting install options for ${g.title}`, "Starting Heroic if needed… this can take up to a minute.");
  let r;
  try { r = await api("/api/install-options", { id: g.id, platform }); } catch { r = { error: "network" }; }
  resume(true);
  if (r.error === "installed") { toast(`${g.title} is already installed in Heroic`, 4000); return; }
  if (r.error) {
    heroicProblem(r.error, `get the install options for <b>${esc(g.title)}</b>`,
      `Or open Heroic yourself, search for <b>${esc(g.title)}</b> and press <b>Install</b>.`, undefined, r);
    return;
  }
  openInstallOptions(g, r);
}

function preferredLanguage(available, preferred) {
  // Same rule as Heroic: the first UI language that prefixes an available one, else the first.
  for (const p of preferred.map(x => x.toLowerCase())) {
    const hit = available.find(a => a.toLowerCase().startsWith(p)) || available.find(a => a.toLowerCase().startsWith(p.split("-")[0]));
    if (hit) return hit;
  }
  return available[0] || "";
}

function openInstallOptions(g, o) {
  const sdlKey = sd => sd.tags.join(",");
  const st = {
    loc: 0,
    lang: o.languages.length ? preferredLanguage(o.languages, o.uiLangs || []) : "",
    dlcs: new Set(o.dlcs.map(d => d.id)),                      // Heroic pre-selects all DLC
    sdl: new Set(o.sdl.filter(sd => sd.required).map(sdlKey)),
    wine: o.wineDefault,
    sharedPrefix: false,          // Heroic's "Use shared Wine prefix" starts off: each game gets its own
  };
  const sizes = () => {
    if (!o.perLang) return { dl: o.download, disk: o.disk, dlcUnknown: st.dlcs.size > 0 };
    const part = (pl, k) => pl ? (pl[st.lang]?.[k] || 0) + (pl["*"]?.[k] || 0) : 0;
    let dl = part(o.perLang, "download_size"), disk = part(o.perLang, "disk_size");
    for (const d of o.dlcs) if (st.dlcs.has(d.id)) { dl += part(d.perLang, "download_size"); disk += part(d.perLang, "disk_size"); }
    return { dl, disk, dlcUnknown: false };
  };
  const toggle = (set, key) => { set.has(key) ? set.delete(key) : set.add(key); };
  const items = () => {
    const loc = o.locations[st.loc] || { label: "?", path: "", free: null };
    const sz = sizes();
    const fits = loc.free == null || !sz.disk || loc.free >= sz.disk;
    const list = [];
    list.push({ label: "Install location", value: `${loc.label} · ${loc.free == null ? "?" : fmtSize(loc.free)} free`,
                change: o.locations.length > 1 ? d => { st.loc = (st.loc + d + o.locations.length) % o.locations.length; } : null });
    if (o.platforms.length > 1)
      list.push({ label: "Platform", value: o.platform === "linux" ? "Linux (native)" : "Windows",
                  change: () => { closeModal(); installViaHeroic(g, o.platform === "linux" ? "Windows" : "linux"); } });
    if (o.languages.length > 1)
      list.push({ label: "Language", value: langName(st.lang),
                  change: d => { st.lang = cycle(o.languages, st.lang, d); } });
    if (o.wine.length)
      list.push({ label: "Wine / Proton", value: (o.wine.find(w => w.bin === st.wine) || o.wine[0]).name,
                  change: d => { st.wine = cycle(o.wine.map(w => w.bin), st.wine, d); } });
    if (o.prefixOwn)
      list.push({ label: "Wine prefix", value: st.sharedPrefix ? `Shared · ${o.prefixShared}` : `Own · ${o.prefixOwn}`,
                  change: () => { st.sharedPrefix = !st.sharedPrefix; } });
    if (o.dlcs.length > 1)
      list.push({ label: "All DLC", value: st.dlcs.size === o.dlcs.length ? "Yes" : st.dlcs.size ? "Some" : "No",
                  change: () => { st.dlcs = st.dlcs.size === o.dlcs.length ? new Set() : new Set(o.dlcs.map(d => d.id)); } });
    for (const d of o.dlcs)
      list.push({ label: "DLC: " + d.title, value: st.dlcs.has(d.id) ? "Yes" : "No", on: st.dlcs.has(d.id), change: () => toggle(st.dlcs, d.id) });
    for (const sd of o.sdl)
      list.push({ label: sd.name, value: sd.required ? "Required" : st.sdl.has(sdlKey(sd)) ? "Yes" : "No", on: st.sdl.has(sdlKey(sd)),
                  change: sd.required ? null : () => toggle(st.sdl, sdlKey(sd)) });
    list.push({ info: true, label: `Download ${fmtSize(sz.dl)} · Installed ${fmtSize(sz.disk)}${sz.dlcUnknown ? " + DLC" : ""}`, value: fits ? null : "Not enough space" });
    list.push({ label: fits ? `Install ${g.title}` : "Not enough free space here", activate: () => {
      if (!fits) { toast("Not enough free space at this location", 3000); return; }
      closeModal();
      startInstall(g, {
        path: loc.path, platform: o.platform, language: st.lang,
        dlcs: o.dlcs.filter(d => st.dlcs.has(d.id)).map(d => d.id),
        sdl: o.sdl.filter(sd => st.sdl.has(sdlKey(sd))).flatMap(sd => sd.tags),
        wine: o.platform === "Windows" ? st.wine : null,
        sharedPrefix: st.sharedPrefix,
      });
    } });
    return list;
  };
  const m = { kind: "list", title: `Install ${g.title}`, hint: "◀ ▶ change · A select · B cancel", items, sel: 0 };
  m.sel = items().length - 1;   // start on the Install button
  openModal(m);
}

// Installs and uninstalls go into the app's queue and run one at a time.
function queuedToast(kind, g, r) {
  if (r.ok) toast(r.position > 1 ? `${kind === "install" ? "Install" : "Uninstall"} of ${g.title} queued (#${r.position})`
                                 : `${kind === "install" ? "Installing" : "Uninstalling"} ${g.title}…`, 3500);
  else if (r.error === "already-queued") toast(`${g.title} is already in the queue`, 3000);
  else heroicProblem(r.error, `${kind} <b>${esc(g.title)}</b>`, "", undefined, r);
  if (r.ok) pollDownloads();
}
async function startInstall(g, options) {
  let r;
  try { r = await api("/api/install-start", { id: g.id, options }); } catch { r = { error: "network" }; }
  queuedToast("install", g, r);
}

// Uninstalling, with the same choices as Heroic's uninstall dialog (both off by default).
async function uninstallFlow(g) {
  pauseFor(`Uninstall ${g.title}`, "Asking Heroic… this can take a moment if Heroic has to start.");
  let o;
  try { o = await api("/api/uninstall-options", { id: g.id }); } catch { o = { error: "network" }; }
  resume(true);
  if (o.error === "not-installed") { toast(`${g.title} isn't installed`, 3000); setTimeout(loadGames, 500); return; }
  if (o.error) { heroicProblem(o.error, `uninstall <b>${esc(g.title)}</b>`, "", undefined, o); return; }
  if (o.epicBusy) {
    openModal({ kind: "confirm", yes: "OK", onYes: () => {}, html: `Heroic can't uninstall an Epic game while another Epic game is installing.
      <div class="note">Try again when the download has finished.</div>` });
    return;
  }
  const st = { prefix: false, settings: false };
  const items = () => {
    const list = [{ info: true, label: "Installed in", value: o.installPath || "?" }];
    if (!o.native && o.prefix) {
      list.push({ label: "Remove Wine prefix", value: o.prefixShared ? "Shared, kept" : st.prefix ? "Yes" : "No", on: st.prefix,
                  change: o.prefixShared ? null : () => { st.prefix = !st.prefix; } });
      list.push({ info: true, label: o.prefixShared ? "This game uses Heroic's shared prefix, which is never removed"
                                                    : "Also deletes save files in the prefix that aren't backed up", value: o.prefix });
    }
    list.push({ label: "Erase settings and log", value: st.settings ? "Yes" : "No", on: st.settings, change: () => { st.settings = !st.settings; } });
    list.push({ label: `Uninstall ${g.title}`, activate: () => { closeModal(); startUninstall(g, st); } });
    return list;
  };
  openModal({ kind: "list", title: `Uninstall ${g.title}?`, hint: "◀ ▶ change · A select · B cancel", items, sel: 1 });
}

async function startUninstall(g, st) {
  let r;
  try { r = await api("/api/uninstall-start", { id: g.id, removePrefix: st.prefix, removeSettings: st.settings }); } catch { r = { error: "network" }; }
  queuedToast("uninstall", g, r);
}
async function cancelQueued(g) {
  closeModal();
  const r = await api("/api/queue-cancel", { id: g.id });
  toast(r.ok ? `Removed ${g.title} from the queue` : `${g.title} has already started`, 3000);
  pollDownloads();
}

// Download progress from Heroic's queue, shown on the game's row and in its details.
S.downloads = {};
function downloadText(g, long = false) {
  const d = S.downloads[g.id];
  if (!d) return "";
  if (d.queued) return d.kind === "uninstall" ? `Uninstall queued #${d.pos}` : `Queued #${d.pos}`;
  if (d.starting) return "Starting…";
  if (d.uninstalling) return "Uninstalling…";
  if (d.finishing) return long ? "Finishing install…" : "Finishing…";
  if (!d.active) return long ? "Queued in Heroic · keep this app open until it finishes" : "Queued";
  const pct = d.percent != null ? Math.round(d.percent) + "%" : "";
  if (!long) return `⬇ ${pct || "Downloading"}`;
  return ["Downloading " + pct, d.speed ? d.speed.toFixed(1) + " MiB/s" : "", d.eta ? d.eta + " left" : "",
          "keep this app open until it finishes"].filter(Boolean).join(" · ");
}
const reportedJobs = new Set();
async function pollDownloads() {
  let d;
  try { d = await api("/api/downloads", {}); } catch { return; }
  if (d.resumed && !S.resumedShown) {
    S.resumedShown = true;
    toast(`Resuming ${d.resumed} queued job${d.resumed === 1 ? "" : "s"} from last time`, 4000);
  }
  const next = {};
  if (d.running) (d.queue || []).forEach((q, i) => {
    const pr = (d.progress || {})[q.appName] || {};
    next[`${q.runner}:${q.appName}`] = { active: i === 0 && d.state === "running", percent: pr.percent,
                                          speed: pr.speed, eta: pr.eta };
  });
  if (d.running) for (const u of d.uninstalling || []) next[`${u.runner}:${u.appName}`] = { uninstalling: true };
  // the app's own queue: waiting jobs, and the running one before Heroic reports on it
  for (const j of d.jobs || []) {
    if (j.state === "queued") next[j.id] = { queued: true, kind: j.kind, pos: j.position };
    else if (j.state === "running" && !next[j.id]) next[j.id] = j.kind === "uninstall" ? { uninstalling: true } : { starting: true };
    else if ((j.state === "failed" || j.state === "done") && !reportedJobs.has(j.n)) {
      reportedJobs.add(j.n);
      const g = S.games.find(x => x.id === j.id);
      if (j.state === "failed") toast(`Couldn't ${j.kind} ${g?.title || "the game"}: ${j.error}`, 6000);
    }
  }
  // Heroic says it's done, but our library may not show the change yet: keep the status
  // ("Uninstalling…" / "Finishing…") until the installed state flips, or for at most 30 s.
  const now = Date.now();
  let waiting = false;
  for (const [id, prev] of Object.entries(S.downloads)) {
    if (next[id]) continue;
    const g = S.games.find(x => x.id === id);
    const was = prev.was ?? g?.installed;
    if (g && g.installed === was && now - (prev.since || now) < 30000) {
      next[id] = prev.uninstalling ? { uninstalling: true, was, since: prev.since || now }
                                   : { finishing: true, was, since: prev.since || now };
      waiting = true;
    }
  }
  const ids = new Set([...Object.keys(next), ...Object.keys(S.downloads)]);
  const changed = [...ids].filter(id => JSON.stringify(next[id]) !== JSON.stringify(S.downloads[id]));
  S.downloads = next;
  for (const id of changed) {
    const g = S.games.find(x => x.id === id);
    if (!g || !rowCache.has(id)) continue;
    const el = rowEl(g);
    if (S.view[S.sel]?.id === id) { el.classList.add("sel"); renderDetail(g); }
  }
  if (waiting) {   // ask the backend to re-read Heroic's install list now, then refresh
    try { await api("/api/reload-library", {}); } catch {}
    await loadGames();
    for (const id of Object.keys(S.downloads)) {   // installed state flipped: drop the status now
      const e = S.downloads[id], g = S.games.find(x => x.id === id);
      if (e.was !== undefined && g && g.installed !== e.was) {
        delete S.downloads[id];
        if (g.installed) afterInstall(g);
        if (rowCache.has(id)) { const el = rowEl(g); if (S.view[S.sel]?.id === id) { el.classList.add("sel"); renderDetail(g); } }
      }
    }
  }
}
setInterval(pollDownloads, 2500);

// Shared explanations when Heroic or Steam can't do what was asked.
const RESTART_HTML = `<div class="note">Restart Steam: <b>Steam button → Power → Restart Steam</b>
  (in Desktop Mode, quit Steam and start it again).</div>`;
function heroicProblem(code, what, extra, g, extraInfo = {}) {
  const title = g ? `<b>${esc(g.title)}</b>` : "this game";
  const msg = {
    "heroic-running": `Heroic is already open, so this app can't ${what}.
      <div class="note">Quit Heroic completely (Desktop Mode: tray icon → Quit; Game Mode: Steam button → Heroic → Exit game), then try again. ${extra}</div>`,
    "heroic-not-started": `Heroic didn't start within 45 seconds, so this app couldn't ${what}.<div class="note">Try again. ${extra}</div>`,
    "restart-steam": `Steam can't launch ${title} yet: it was added to Steam after Steam started. ${RESTART_HTML}`,
    "no-shortcut": `${title} isn't in Steam, so this app won't launch it (it would close when this app closes).
      <div class="note">In Heroic, open the game and choose <b>Add to Steam</b>, then restart Steam.</div>`,
    "steam-not-running": `Steam isn't running. Start Steam, then try again.`,
    "heroic-busy": `Heroic is still busy (downloading or uninstalling), so ${title} can't be launched yet.
      <div class="note">Launching closes the Heroic this app runs in the background, which would stop that. Try again when it's finished.</div>`,
    "heroic-unsupported": `${esc(extraInfo.detail || "This Heroic isn't supported yet")}, so this app can't ${what}.
      <div class="note">Do it in Heroic itself for now, or update Heroic Ratings once a version that supports it is out.</div>`,
    "heroic-api-changed": `Heroic has changed in a way this app doesn't expect, so it won't ${what} (rather than risk a half-done job).
      <div class="note">Do it in Heroic itself for now. (${esc(extraInfo.detail || "")})</div>`,
    "heroic-store-unmanaged": `${esc(extraInfo.detail || "These games are installed in Heroic itself for now")}, so this app can't ${what}.`,
    "heroic-not-stopped": `The Heroic this app runs in the background didn't close, so ${title} wasn't launched. Try again.`,
  }[code];
  openModal({ kind: "confirm", yes: "OK", onYes: () => {},
    html: msg || `Couldn't ${what} <span class="note">(${esc(code || "unknown")})</span>.<div class="note">${extra}</div>` });
}

// Once at start-up: remove the "Heroic (Debug)" shortcut an earlier version added, and
// note which game shortcuts exist now (to tell later which ones Steam hasn't loaded).
api("/api/steam-setup", {}).then(r => { if (r.removed_old_shortcut) toast("Removed the old Heroic (Debug) shortcut from Steam", 4000); }).catch(() => {});

// After an install: Steam can only launch the game through the shortcut Heroic adds,
// once Steam has been restarted. Heroic adds it as the install finishes, so wait a bit.
async function afterInstall(g) {
  let st = "missing";
  for (let i = 0; i < 10; i++) {
    try { st = (await api("/api/game-shortcut", { id: g.id })).status; } catch {}
    if (st !== "missing") break;
    await new Promise(r => setTimeout(r, 1000));
  }
  if (st !== "restart" && st !== "missing") { toast(`${g.title} is installed`, 3500); return; }
  openModal({ kind: "confirm", yes: "OK", onYes: () => {}, html: st === "restart"
    ? `<b>${esc(g.title)}</b> is installed. ${RESTART_HTML.replace("Restart Steam:", "To play it, restart Steam:")}`
    : `<b>${esc(g.title)}</b> is installed, but Heroic didn't add it to Steam, so this app can't launch it.
      <div class="note">In Heroic, open the game and choose <b>Add to Steam</b> (or turn on <i>Add games to Steam automatically</i> in Heroic's settings), then restart Steam.</div>` });
}

function focusSearch() {
  els.search.focus();
  els.search.select();
}
// Y / Menu → Search: on a Steam Deck the app's own keyboard (Steam's keyboard is unreliable
// for non-Steam apps); on a PC, the search box itself.
function openSearch() {
  if (!S.steamDeck) { focusSearch(); return; }
  const before = S.query;
  openKeyboard({ title: "Search", value: S.query,
    onChange: v => { S.query = v; els.search.value = v; buildView(); select(0, true); },
    onCancel: () => { S.query = before; els.search.value = before; buildView(); select(0, true); } });
}

