"use strict";

// ---------------------------------------------------------------- config
const SORTS = [
  { key: "rating", label: "Rating", dir: -1, get: g => g.rating },
  { key: "percent", label: "% Positive", dir: -1, get: g => g.percent },
  { key: "reviews", label: "Popularity", dir: -1, get: g => g.reviews || null },
  { key: "metascore", label: "Metascore", dir: -1, get: g => g.metascore },
  { key: "year", label: "Release year", dir: -1, get: g => g.year },
  { key: "title", label: "Title", dir: 1, get: g => g.title.toLowerCase() },
];
const STORES = ["All", "Epic", "GOG", "Amazon", "Other"];
const PHASE_TEXT = { starting: "Reading Heroic library…", matching: "Matching titles on Steam", wikidata: "Looking up the rest on Wikidata", ratings: "Fetching Steam reviews" };

// ---------------------------------------------------------------- state
const S = {
  games: [], view: [], sel: 0, version: -1, progress: {},
  sortIdx: 0, dir: -1, genre: null, store: "All", installedOnly: false, hideUnrated: false, query: "",
  modal: null,
};
const $ = id => document.getElementById(id);
const els = { list: $("list"), detail: $("detail"), search: $("search"), overlay: $("overlay"), modal: $("modal") };

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("hr-prefs") || "{}");
    for (const k of ["sortIdx", "dir", "genre", "store", "installedOnly", "hideUnrated"]) if (k in p) S[k] = p[k];
    if (!SORTS[S.sortIdx]) S.sortIdx = 0;
  } catch (e) { /* storage unavailable */ }
}
function savePrefs() {
  try {
    const { sortIdx, dir, genre, store, installedOnly, hideUnrated } = S;
    localStorage.setItem("hr-prefs", JSON.stringify({ sortIdx, dir, genre, store, installedOnly, hideUnrated }));
  } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- helpers
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = s => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, " ").trim();
const fmtN = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? Math.round(n / 1e3) + "k" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
const scoreClass = p => p == null ? "c-na" : p >= 70 ? "c-pos" : p >= 40 ? "c-mix" : "c-neg";
const mcClass = m => m >= 75 ? "mc-hi" : m >= 50 ? "mc-mid" : "mc-lo";

function img(url, w) {
  if (!url) return "";
  // Epic's CDN can resize on the fly, which keeps memory use low on the Deck.
  if (/epicgames\.com|unrealengine\.com/.test(url) && !url.includes("?")) return `${url}?w=${w}&resize=1`;
  return url;
}

// What to show in the score slot: Steam user %, else a critic score, else why it's unrated.
function scoreParts(g) {
  if (g.percent != null) return { big: g.percent + "%", cls: scoreClass(g.percent), label: g.label };
  if (g.critic != null) return { big: String(g.critic), cls: scoreClass(g.critic), label: "Critics · " + g.critic_by };
  return { big: "—", cls: "c-na", label: unratedReason(g) };
}

function unratedReason(g) {
  if (g.extra) return "Extra / tool";
  if (!g.matched) return "Fetching…";
  if (!g.appid) return "Not on Steam";
  if (g.percent == null) return g.reviews === 0 ? "No reviews" : "Fetching…";
  return "";
}

function genreCounts() {
  const c = new Map();
  for (const g of S.games) for (const t of g.genres) c.set(t, (c.get(t) || 0) + 1);
  return [...c.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

// ---------------------------------------------------------------- view building
function buildView(keepId) {
  const q = norm(S.query);
  const sort = SORTS[S.sortIdx];
  let v = S.games.filter(g =>
    (!S.genre || g.genres.includes(S.genre) || g.tags.includes(S.genre)) &&
    (S.store === "All" || g.store === S.store) &&
    (!S.installedOnly || g.installed) &&
    (!S.hideUnrated || g.percent != null || g.critic != null) &&
    (!q || norm(g.title).includes(q) || norm(g.steam_name || "").includes(q)));
  v.sort((a, b) => {
    const x = sort.get(a), y = sort.get(b);
    if (x == null && y == null) return a.title.localeCompare(b.title);
    if (x == null) return 1;
    if (y == null) return -1;
    if (x < y) return -S.dir;
    if (x > y) return S.dir;
    return (b.reviews || 0) - (a.reviews || 0) || a.title.localeCompare(b.title);
  });
  S.view = v;
  const idx = keepId ? v.findIndex(g => g.id === keepId) : -1;
  S.sel = idx >= 0 ? idx : keepId ? 0 : Math.min(S.sel, Math.max(0, v.length - 1));
  renderAll();
}

// Rows are built once per game and re-used: sorting/filtering only reorders
// existing nodes, so cover images aren't re-downloaded or re-decoded.
const rowCache = new Map(); // id -> { html, el }
function rowEl(g) {
  const html = rowHTML(g), score = rowScoreHTML(g);
  let c = rowCache.get(g.id);
  if (!c || c.html !== html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    const el = t.content.firstElementChild;
    if (c?.el.parentNode) c.el.replaceWith(el);
    c = { html, el, score: null };
    rowCache.set(g.id, c);
  }
  // The score column follows the sort, so only it changes when the sort does
  // (the rest of the row, including its cover image, is left alone).
  if (c.score !== score) { c.el.querySelector(".score").innerHTML = score; c.score = score; }
  return c.el;
}

function rowHTML(g) {
  const dl = downloadText(g);
  const sub = [g.store, g.installed && !dl ? '<span class="inst">● Installed</span>' : "", dl ? `<span class="dl">${esc(dl)}</span>` : "", esc(g.genres.slice(0, 3).join(", "))].filter(Boolean).join(" · ");
  return `<div class="row" data-id="${esc(g.id)}">
    ${g.art ? `<img loading="lazy" decoding="async" src="${esc(img(g.art, 120))}" alt="">` : '<div class="noimg"></div>'}
    <div class="t"><div class="name">${esc(g.title)}</div><div class="sub">${sub}</div></div>
    <div class="score"></div>
  </div>`;
}

// The one value shown in the list: whatever the current sort orders by.
const mcScoreClass = m => m >= 75 ? "c-pos" : m >= 50 ? "c-mix" : "c-neg";
function sortValue(g) {
  const none = label => ({ big: "—", cls: "c-na", label });
  switch (SORTS[S.sortIdx].key) {
    case "percent":
      if (g.percent != null) return { big: g.percent + "%", cls: scoreClass(g.percent), label: g.label };
      return none(g.critic != null ? "No Steam reviews" : unratedReason(g));
    case "reviews":
      if (g.reviews) return { big: fmtN(g.reviews), cls: "c-plain", label: g.reviews === 1 ? "review" : "reviews" };
      return none(g.critic != null ? "No Steam reviews" : unratedReason(g));
    case "metascore":
      return g.metascore ? { big: String(g.metascore), cls: mcScoreClass(g.metascore), label: "Metascore" } : none("No Metascore");
    case "year":
      return g.year ? { big: String(g.year), cls: "c-plain", label: "Released" } : none("Release date unknown");
    default: // Rating (and Title, which has no number of its own)
      if (g.percent != null && g.rating != null) return { big: Math.round(g.rating), cls: scoreClass(g.rating), label: g.label };
      if (g.critic != null) return { big: String(g.critic), cls: scoreClass(g.critic), label: "Critics · " + g.critic_by };
      return none(unratedReason(g));
  }
}
function rowScoreHTML(g) {
  const v = sortValue(g);
  return `<div class="pct ${v.cls}">${esc(String(v.big))}</div><div class="lbl">${esc(v.label || "")}</div>`;
}

function renderAll() {
  const sort = SORTS[S.sortIdx];
  $("sortLabel").textContent = sort.label;
  $("sortDir").textContent = S.dir < 0 ? "▼" : "▲";
  $("genreLabel").textContent = S.genre || "All";
  const f = [];
  if (S.store !== "All") f.push(S.store);
  if (S.installedOnly) f.push("Installed");
  if (S.hideUnrated) f.push("Rated only");
  $("chipFilters").textContent = f.join(" · ");
  $("count").textContent = `${S.view.length} / ${S.games.length} games`;

  if (!S.view.length) {
    const msg = !S.games.length
      ? (S.progress.phase === "error" ? esc(S.progress.message) : "Loading your Heroic library…")
      : "No games match these filters.<br>Press <span class='btn b'>B</span> to clear.";
    els.list.innerHTML = `<div class="empty">${msg}</div>`;
    els.detail.innerHTML = "";
    return;
  }
  const frag = document.createDocumentFragment();
  for (const g of S.view) frag.appendChild(rowEl(g));
  els.list.replaceChildren(frag);
  els.list.querySelectorAll(".row.sel").forEach(r => r.classList.remove("sel"));
  select(S.sel, true);
}

function select(i, force) {
  if (!S.view.length) return;
  i = Math.max(0, Math.min(S.view.length - 1, i));
  if (i === S.sel && !force) return;
  els.list.querySelector(".row.sel")?.classList.remove("sel");
  S.sel = i;
  const row = rowCache.get(S.view[i].id)?.el;
  if (row) {
    row.classList.add("sel");
    row.scrollIntoView({ block: "nearest" });
  }
  renderDetail(S.view[i]);
}

function renderDetail(g) {
  const p = g.percent;
  const sc = scoreParts(g);
  const showSteamName = g.steam_name && norm(g.steam_name) !== norm(g.title);
  const genres = g.genres;
  const others = g.tags.filter(t => !genres.includes(t)).slice(0, 8);
  els.detail.innerHTML = `
    ${g.art ? `<img class="cover" src="${esc(img(g.art, 480))}" alt="">` : ""}
    <h2>${esc(g.title)}</h2>
    <div class="matched">${g.appid ? (showSteamName ? "Steam match: " + esc(g.steam_name) : "Steam app " + g.appid) + (g.via === "wikidata" ? " (found via Wikidata)" : "") + (g.override ? " (manual)" : "") : g.critic != null ? "Not on Steam" : esc(unratedReason(g))}</div>
    <div class="bigscore">
      <div><div class="pct ${sc.cls}">${sc.big}</div></div>
      <div><div class="lbl ${sc.cls}">${esc(sc.label)}</div>
        <div class="n">${g.reviews ? g.reviews.toLocaleString() + " Steam reviews" : p == null && g.critic != null ? "Critic average out of 100" : ""}</div>
        <div class="n">${p != null && g.rating != null ? "Weighted rating " + g.rating.toFixed(1) : ""}</div>
        <div class="n">${p != null && g.critic != null ? `Critics: ${g.critic} (${esc(g.critic_by)})` : ""}</div></div>
      ${g.metascore ? `<div class="meta ${mcClass(g.metascore)}" title="Metascore">${g.metascore}</div>` : ""}
    </div>
    <div class="facts">${g.year ? `Released <b>${g.year}</b>` : ""}${g.year && g.devs?.length ? " · " : ""}${g.devs?.length ? `<b>${esc(g.devs.slice(0, 2).join(", "))}</b>` : ""}</div>
    <div class="tags">${genres.map(t => `<span class="tag g">${esc(t)}</span>`).join("")}${others.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    ${g.desc ? `<p>${esc(g.desc)}</p>` : ""}
    <div class="state">${downloadText(g, true) ? `<span class="dl">${esc(downloadText(g, true))}</span>` : g.installed ? '<span class="inst">● Installed</span>' : "Not installed"} · ${esc(g.store)}</div>`;
}

// ---------------------------------------------------------------- data
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

async function loadGames() {
  const d = await api("/api/games");
  const keep = S.view[S.sel]?.id;
  S.games = d.games;
  S.steamDeck = !!d.steam_deck;
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

// ---------------------------------------------------------------- toast
let toastTimer;
function toast(msg, ms = 2500) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

// ---------------------------------------------------------------- modals
function openModal(m) {
  S.modal = m;
  m.sel = m.sel || 0;
  renderModal();
  els.overlay.hidden = false;
}
function closeModal() {
  S.modal = null;
  els.overlay.hidden = true;
}
function renderModal() {
  const m = S.modal;
  els.modal.className = "modal " + (m.kind || "");
  if (m.kind === "confirm") {
    els.modal.innerHTML = `<div class="q">${m.html}</div>
      <div class="hint"><span><span class="btn a">A</span>${esc(m.yes || "OK")}</span><span><span class="btn b">B</span>Cancel</span></div>`;
    return;
  }
  const items = m.items();
  m.sel = Math.max(0, Math.min(items.length - 1, m.sel));
  els.modal.innerHTML = `<h3>${esc(m.title)}<small>${m.hint || ""}</small></h3>
    <div class="opts">${items.map((it, i) => `<div class="opt ${i === m.sel ? "sel" : ""} ${it.on ? "on" : ""} ${it.info ? "info" : ""}" data-i="${i}">
      <span>${esc(it.label)}</span>${it.value != null ? `<span class="v ${it.change ? "arrows" : ""}">${esc(it.value)}</span>` : ""}</div>`).join("")}</div>`;
  els.modal.querySelector(".opt.sel")?.scrollIntoView({ block: "nearest" });
}

function modalInput(a) {
  const m = S.modal;
  if (m.kind === "confirm") {
    if (a === "a") { closeModal(); m.onYes(); }
    else if (a === "b" || a === "start") closeModal();
    return;
  }
  const items = m.items();
  const cols = m.kind === "grid" ? 4 : 1;
  const it = items[m.sel];
  switch (a) {
    case "up": m.sel = Math.max(0, m.sel - cols); break;
    case "down": m.sel = Math.min(items.length - 1, m.sel + cols); break;
    case "left": if (cols > 1) m.sel = Math.max(0, m.sel - 1); else it?.change?.(-1); break;
    case "right": if (cols > 1) m.sel = Math.min(items.length - 1, m.sel + 1); else it?.change?.(1); break;
    case "lt": m.sel = Math.max(0, m.sel - cols * 6); break;
    case "rt": m.sel = Math.min(items.length - 1, m.sel + cols * 6); break;
    case "a": if (it) { if (it.activate) it.activate(); else it.change?.(1); } break;
    case "b": case "start": case "y": closeModal(); return;
  }
  if (S.modal === m) renderModal();
}

function openGenres() {
  const list = genreCounts();
  const cur = S.genre ? list.findIndex(([n]) => n === S.genre) + 1 : 0;
  openModal({
    kind: "grid", title: "Filter by genre",
    hint: `<span><span class="btn a">A</span>Select</span><span><span class="btn b">B</span>Close</span>`,
    sel: cur,
    items: () => [{ label: "All genres", value: S.games.length, on: !S.genre, activate: () => setGenre(null) },
      ...list.map(([n, c]) => ({ label: n, value: c, on: S.genre === n, activate: () => setGenre(n) }))],
  });
}
function setGenre(g) {
  S.genre = g;
  closeModal();
  savePrefs();
  buildView(S.view[S.sel]?.id);
  els.list.scrollTop = 0;
  select(0, true);
}
function cycleGenre(d) {
  const names = [null, ...genreCounts().map(([n]) => n)];
  const i = names.indexOf(S.genre);
  S.genre = names[(i + d + names.length) % names.length];
  savePrefs();
  buildView();
  select(0, true);
}

function cycle(list, cur, d) { return list[(list.indexOf(cur) + d + list.length) % list.length]; }

function openMenu() {
  const g = S.view[S.sel];
  const storesPresent = STORES.filter(s => s === "All" || S.games.some(x => x.store === s));
  const refilter = () => { savePrefs(); buildView(S.view[S.sel]?.id); };
  openModal({
    title: "Menu",
    hint: `<span>◀ ▶ change</span><span><span class="btn a">A</span>Select</span><span><span class="btn b">B</span>Close</span>`,
    items: () => [
      { label: "Sort by", value: SORTS[S.sortIdx].label, change: d => { setSort((S.sortIdx + d + SORTS.length) % SORTS.length); } },
      { label: "Order", value: S.dir < 0 ? "Highest first" : "Lowest first", change: () => { S.dir = -S.dir; refilter(); } },
      { label: "Store", value: S.store, change: d => { S.store = cycle(storesPresent, S.store, d); refilter(); } },
      { label: "Installed only", value: S.installedOnly ? "On" : "Off", change: () => { S.installedOnly = !S.installedOnly; refilter(); } },
      { label: "Hide unrated games", value: S.hideUnrated ? "On" : "Off", change: () => { S.hideUnrated = !S.hideUnrated; refilter(); } },
      { label: "Search…", activate: () => { closeModal(); focusSearch(); } },
      { label: "Controller test", activate: () => { closeModal(); openDiag(); } },
      ...(g && g.installed && !S.downloads[g.id] ? [{ label: `Uninstall “${g.title}”`, activate: () => { closeModal(); uninstallFlow(g); } }] : []),
      ...(g && g.appid ? [{ label: `Wrong Steam match for “${g.title}”`, activate: () => wrongMatch(g) }] : []),
      { label: "Refresh library & new ratings", activate: () => { api("/api/refresh", {}); closeModal(); toast("Refreshing…"); } },
      { label: "Re-fetch all ratings", activate: () => { api("/api/refresh", { force: true }); closeModal(); toast("Re-fetching everything from Steam…"); } },
      { label: "Quit", activate: quit },
    ],
  });
}

function wrongMatch(g) {
  openModal({
    kind: "confirm", yes: "Remove match",
    html: `Remove the Steam match <b>${esc(g.steam_name || g.appid)}</b> from <b>${esc(g.title)}</b>?<br><small>You can set the right app ID in data/overrides.json</small>`,
    onYes: async () => { await api("/api/override", { id: g.id, appid: null }); toast("Match removed"); loadGames(); },
  });
}

async function quit() {
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
      `Or open Heroic yourself, search for <b>${esc(g.title)}</b> and press <b>Install</b>.`);
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

async function startInstall(g, options) {
  let r;
  try { r = await api("/api/install-start", { id: g.id, options }); } catch { r = { error: "network" }; }
  if (r.result === "queued") { toast(`${g.title} added to Heroic's downloads`, 3500); setTimeout(pollDownloads, 1000); }
  else if (r.result === "installed") toast(`${g.title} is already installed`, 3500);
  else heroicProblem(r.error || r.result, `install <b>${esc(g.title)}</b>`, "");
}

// Uninstalling, with the same choices as Heroic's uninstall dialog (both off by default).
async function uninstallFlow(g) {
  pauseFor(`Uninstall ${g.title}`, "Asking Heroic… this can take a moment if Heroic has to start.");
  let o;
  try { o = await api("/api/uninstall-options", { id: g.id }); } catch { o = { error: "network" }; }
  resume(true);
  if (o.error === "not-installed") { toast(`${g.title} isn't installed`, 3000); setTimeout(loadGames, 500); return; }
  if (o.error) { heroicProblem(o.error, `uninstall <b>${esc(g.title)}</b>`, ""); return; }
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
  // Show "Uninstalling…" straight away; pollDownloads keeps it until the library shows the game as removed.
  const mark = on => {
    if (on) S.downloads[g.id] = { uninstalling: true, was: true, since: Date.now() }; else delete S.downloads[g.id];
    if (rowCache.has(g.id)) { const el = rowEl(g); if (S.view[S.sel]?.id === g.id) { el.classList.add("sel"); renderDetail(g); } }
  };
  mark(true);
  let r;
  try { r = await api("/api/uninstall-start", { id: g.id, removePrefix: st.prefix, removeSettings: st.settings }); } catch { r = { error: "network" }; }
  if (r.result === "started" || r.result === "done") { toast(`Uninstalling ${g.title}…`, 3000); setTimeout(pollDownloads, 800); }
  else { mark(false); heroicProblem(r.error || r.result, `uninstall <b>${esc(g.title)}</b>`, ""); }
}

// Download progress from Heroic's queue, shown on the game's row and in its details.
S.downloads = {};
function downloadText(g, long = false) {
  const d = S.downloads[g.id];
  if (!d) return "";
  if (d.uninstalling) return "Uninstalling…";
  if (d.finishing) return long ? "Finishing install…" : "Finishing…";
  if (!d.active) return long ? "Queued in Heroic · keep this app open until it finishes" : "Queued";
  const pct = d.percent != null ? Math.round(d.percent) + "%" : "";
  if (!long) return `⬇ ${pct || "Downloading"}`;
  return ["Downloading " + pct, d.speed ? d.speed.toFixed(1) + " MiB/s" : "", d.eta ? d.eta + " left" : "",
          "keep this app open until it finishes"].filter(Boolean).join(" · ");
}
async function pollDownloads() {
  let d;
  try { d = await api("/api/downloads", {}); } catch { return; }
  const next = {};
  if (d.running) (d.queue || []).forEach((q, i) => {
    const pr = (d.progress || {})[q.appName] || {};
    next[`${q.runner}:${q.appName}`] = { active: i === 0 && d.state === "running", percent: pr.progress?.percent,
                                          speed: pr.progress?.downSpeed, eta: pr.progress?.eta };
  });
  if (d.running) for (const u of d.uninstalling || []) next[`${u.runner}:${u.appName}`] = { uninstalling: true };
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
function heroicProblem(code, what, extra, g) {
  const title = g ? `<b>${esc(g.title)}</b>` : "this game";
  const msg = {
    "heroic-running": `Heroic is already open, so this app can't ${what}.
      <div class="note">Quit Heroic completely (Desktop Mode: tray icon → Quit; Game Mode: Steam button → Heroic → Exit game), then try again. ${extra}</div>`,
    "heroic-not-started": `Heroic didn't start within 45 seconds, so this app couldn't ${what}.<div class="note">Try again. ${extra}</div>`,
    "restart-steam": `Steam can't launch ${title} yet: it was added to Steam after Steam started. ${RESTART_HTML}`,
    "no-shortcut": `${title} isn't in Steam, so this app won't launch it (it would close when this app closes).
      <div class="note">In Heroic, open the game and choose <b>Add to Steam</b>, then restart Steam.</div>`,
    "steam-not-running": `Steam isn't running. Start Steam, then try again.`,
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

// ---------------------------------------------------------------- main input
function pageSize() {
  const row = els.list.querySelector(".row");
  return row ? Math.max(1, Math.floor(els.list.clientHeight / row.offsetHeight) - 1) : 8;
}

function input(a) {
  document.body.classList.add("nomouse");
  if (S.modal) return modalInput(a);
  if (document.activeElement === els.search) {
    if (["b", "a", "down", "start"].includes(a)) els.search.blur();
    return;
  }
  switch (a) {
    case "up": select(S.sel - 1); break;
    case "down": select(S.sel + 1); break;
    case "left": case "lt": select(S.sel - pageSize()); break;
    case "right": case "rt": select(S.sel + pageSize()); break;
    case "home": select(0); break;
    case "end": select(S.view.length - 1); break;
    case "a": launchSelected(); break;
    case "b":
      if (S.query || S.genre || S.store !== "All" || S.installedOnly || S.hideUnrated) {
        S.query = ""; els.search.value = ""; S.genre = null; S.store = "All"; S.installedOnly = false; S.hideUnrated = false;
        savePrefs(); buildView(S.view[S.sel]?.id); toast("Filters cleared");
      } else select(0);
      break;
    case "x": setSort((S.sortIdx + 1) % SORTS.length); toast("Sort: " + SORTS[S.sortIdx].label, 1200); break;
    case "select": S.dir = -S.dir; savePrefs(); buildView(S.view[S.sel]?.id); break;
    case "y": openGenres(); break;
    case "lb": cycleGenre(-1); break;
    case "rb": cycleGenre(1); break;
    case "start": openMenu(); break;
  }
}

// Gamepad (standard mapping; Steam Input presents an Xbox-style pad)
const PAD = { a: 0, b: 1, x: 2, y: 3, lb: 4, rb: 5, lt: 6, rt: 7, select: 8, start: 9, up: 12, down: 13, left: 14, right: 15 };
const REPEAT = new Set(["up", "down", "left", "right", "lt", "rt"]);
const held = {};
function readPads(scroll = true) {
  const st = {};
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const p of pads) {
    if (!p || !p.connected) continue;
    for (const [k, i] of Object.entries(PAD)) {
      const b = p.buttons[i];
      if (b && (b.pressed || b.value > 0.5)) st[k] = true;
    }
    const ax = p.axes[0] || 0, ay = p.axes[1] || 0;
    if (ay < -0.55) st.up = true;
    if (ay > 0.55) st.down = true;
    if (ax < -0.7) st.left = true;
    if (ax > 0.7) st.right = true;
    const ry = p.axes[3] || 0;  // right stick scrolls the details panel
    if (scroll && Math.abs(ry) > 0.25) els.detail.scrollTop += ry * 14;
  }
  return st;
}
// Never let an error in one action kill controller input for good.
function safeInput(a) {
  try { input(a); } catch (e) { console.error(e); toast("Error: " + e.message, 6000); }
}
// ---------------------------------------------------------------- pause while Heroic/a game is in front
// The Gamepad API keeps reporting buttons even when another window (Heroic, a game)
// is on top, so we ignore the pad when unfocused, and after launching/installing we
// pause until the user is back here.
let paused = null; // { sawBlur, comboStart }
function pauseFor(title, sub) {
  paused = { sawBlur: !document.hasFocus(), comboStart: 0 };
  $("pausedTitle").textContent = title;
  $("pausedSub").textContent = sub;
  $("paused").hidden = false;
}
function resume(quiet = false) {
  if (!paused) return;
  paused = null;
  $("paused").hidden = true;
  if (!quiet) toast("Controls resumed", 1200);
}
window.addEventListener("blur", () => { if (paused) paused.sawBlur = true; });
window.addEventListener("focus", () => { if (paused && paused.sawBlur) resume(); });
$("resumeBtn").addEventListener("click", resume);

// Mark currently pressed buttons as "held" so they don't fire once we're active again.
function swallowHeld(st, now) {
  for (const k of Object.keys(PAD)) {
    if (st[k]) held[k] = held[k] || { start: now, next: Infinity };
    else delete held[k];
  }
}

// ---------------------------------------------------------------- controller test screen
// Shows exactly what the app receives (raw gamepad buttons and keyboard keys), so button
// mapping problems on a particular setup can be diagnosed. Nothing acts while it's open.
let diag = null; // { keys: [], holdA: 0, enterDown: 0 }
function openDiag() {
  diag = { keys: [], holdA: 0, enterDown: 0 };
  $("diag").hidden = false;
}
function closeDiag() {
  diag = null;
  $("diag").hidden = true;
  swallowHeld(readPads(false), performance.now()); // buttons still held must not act
}
$("diagClose").addEventListener("click", closeDiag);
function renderDiag(now) {
  const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
  let aDown = false;
  const lines = [`Window focused: ${document.hasFocus() ? "yes" : "no"}   Steam Deck: ${S.steamDeck ? "yes" : "no"}`, ""];
  if (!pads.length) lines.push("No gamepads seen (press a button)");
  pads.forEach((p, n) => {
    const down = p.buttons.map((b, i) => (b.pressed || b.value > 0.5 ? i : null)).filter(i => i !== null);
    if (down.includes(0)) aDown = true;
    const axes = p.axes.map(a => a.toFixed(1)).join(" ");
    lines.push(`Pad ${n}: ${p.id}`, `  mapping: ${p.mapping || "(none)"}   buttons: ${p.buttons.length}`,
               `  pressed: ${down.length ? down.join(", ") : "-"}`, `  axes: ${axes}`, "");
  });
  lines.push("Keyboard keys received (newest first):", ...(diag.keys.length ? diag.keys : ["-"]));
  $("diagOut").textContent = lines.join("\n");
  // Hold A (button 0) for 2 s to close.
  if (aDown) { diag.holdA = diag.holdA || now; if (now - diag.holdA > 2000) closeDiag(); }
  else diag.holdA = 0;
}

function padLoop(now) {
  requestAnimationFrame(padLoop);  // schedule first, so an exception can't stop the loop
  notePadButtons();
  if (diag) { renderDiag(now); return; }
  if (!document.hasFocus()) {      // another window has focus: never act on the pad
    swallowHeld(readPads(false), now);
    return;
  }
  const st = readPads(!paused);
  if (paused) {
    // Manual resume: hold View + Menu together for 1 second.
    if (st.select && st.start) {
      paused.comboStart = paused.comboStart || now;
      if (now - paused.comboStart > 1000) resume();
    } else paused.comboStart = 0;
    swallowHeld(st, now);
    return;
  }
  for (const k of Object.keys(PAD)) {
    if (st[k]) {
      const h = held[k];
      if (!h) {
        held[k] = { start: now, next: now + 350 };
        safeInput(k);
      } else if (REPEAT.has(k) && now >= h.next) {
        const fast = now - h.start > 1500;
        h.next = now + (k === "lt" || k === "rt" ? 180 : fast ? 35 : 70);
        safeInput(k);
      }
    } else delete held[k];
  }
}

// Keyboard. Steam's Desktop layout for the Deck (controller_base/desktop_neptune.vdf)
// sends A = Enter, B and Menu = Escape, View = Tab, Y = Space, LB = Ctrl, RB = Alt,
// back grips = PageDown/PageUp, and keeps X for the on-screen keyboard. Escape opens
// the menu (or closes whatever is open) because B and Menu can't be told apart.

// ---------------------------------------------------------------- Desktop-layout warning
// In Desktop Mode Steam turns the Deck's buttons into keys (X opens the on-screen
// keyboard, B and Menu both send Escape). Warn until a real gamepad button arrives,
// which means the player has switched Steam to its Gamepad layout.
let padUsed = false, layoutChecked = false;
function showLayoutWarn(on) { $("layoutWarn").hidden = !on || padUsed; }
function notePadButtons() {
  if (padUsed || !navigator.getGamepads) return;
  for (const p of navigator.getGamepads()) {
    if (p && p.connected && [0, 1, 2, 3, 8, 9].some(i => p.buttons[i]?.pressed)) { padUsed = true; showLayoutWarn(false); return; }
  }
}
const KEYS = {
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "a", " ": "y",
  Control: "lb", Alt: "rb",
  Escape: "start", Backspace: "b", x: "x", s: "x", y: "y", g: "y", q: "lb", "[": "lb", e: "rb", "]": "rb",
  PageUp: "lt", PageDown: "rt", r: "select", m: "start", Tab: "select", Home: "home", End: "end",
};
document.addEventListener("keydown", e => {
  if (diag) {  // controller test: log keys, act on nothing (holding Enter for 2 s closes)
    e.preventDefault();
    if (!e.repeat) diag.keys.unshift(`${e.key}  (code ${e.code})`);
    diag.keys = diag.keys.slice(0, 8);
    if (e.key === "Enter") { if (!e.repeat) diag.enterDown = Date.now(); else if (Date.now() - diag.enterDown > 2000) closeDiag(); }
    return;
  }
  if (paused) { e.preventDefault(); resume(); return; }  // a key event means this window has focus
  if (document.activeElement === els.search) {
    if (["Escape", "Enter", "ArrowDown", "Tab"].includes(e.key)) { e.preventDefault(); els.search.blur(); }
    return;
  }
  if (e.key === "/") { e.preventDefault(); focusSearch(); return; }
  const a = KEYS[e.key];
  if (a) { e.preventDefault(); safeInput(a); }
  if (S.steamDeck && (e.key === "Tab" || e.key === "Escape")) showLayoutWarn(true);  // Steam's Desktop layout sends these
});

els.search.addEventListener("input", () => { S.query = els.search.value; buildView(); select(0, true); });

// Mouse / touch
document.addEventListener("mousemove", () => document.body.classList.remove("nomouse"));
els.list.addEventListener("click", e => {
  const r = e.target.closest(".row");
  if (r) select(S.view.findIndex(g => g.id === r.dataset.id));
});
els.list.addEventListener("dblclick", e => { if (e.target.closest(".row")) launchSelected(); });
els.overlay.addEventListener("click", e => {
  if (e.target === els.overlay) return closeModal();
  const o = e.target.closest(".opt");
  if (o && S.modal) { S.modal.sel = +o.dataset.i; modalInput("a"); }
});
$("chipSort").addEventListener("click", () => input("x"));
$("chipGenre").addEventListener("click", () => input("y"));

// ---------------------------------------------------------------- boot
loadPrefs();
loadGames().then(poll, () => setTimeout(poll, 2000));
requestAnimationFrame(padLoop);
