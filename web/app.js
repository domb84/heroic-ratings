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
const PHASE_TEXT = { starting: "Reading Heroic library…", matching: "Matching titles on Steam", ratings: "Fetching Steam reviews" };

// ---------------------------------------------------------------- state
const S = {
  games: [], view: [], sel: 0, version: -1, progress: {},
  sortIdx: 0, dir: -1, genre: null, store: "All", installedOnly: false, hideUnrated: false, query: "",
  modal: null,
  swapMenu: "auto", // "auto" | "on" | "off": swap the View/Menu buttons (Steam Deck reports them reversed)
};
const $ = id => document.getElementById(id);
const els = { list: $("list"), detail: $("detail"), search: $("search"), overlay: $("overlay"), modal: $("modal") };

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("hr-prefs") || "{}");
    for (const k of ["sortIdx", "dir", "genre", "store", "installedOnly", "hideUnrated", "swapMenu"]) if (k in p) S[k] = p[k];
    if (!SORTS[S.sortIdx]) S.sortIdx = 0;
  } catch (e) { /* storage unavailable */ }
}
function savePrefs() {
  try {
    const { sortIdx, dir, genre, store, installedOnly, hideUnrated, swapMenu } = S;
    localStorage.setItem("hr-prefs", JSON.stringify({ sortIdx, dir, genre, store, installedOnly, hideUnrated, swapMenu }));
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

function unratedReason(g) {
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
    (!S.hideUnrated || g.percent != null) &&
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
  const html = rowHTML(g);
  let c = rowCache.get(g.id);
  if (!c || c.html !== html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    const el = t.content.firstElementChild;
    if (c?.el.parentNode) c.el.replaceWith(el);
    c = { html, el };
    rowCache.set(g.id, c);
  }
  return c.el;
}

function rowHTML(g) {
  const p = g.percent;
  const reason = unratedReason(g);
  const sub = [g.store, g.installed ? '<span class="inst">● Installed</span>' : "", esc(g.genres.slice(0, 3).join(", "))].filter(Boolean).join(" · ");
  return `<div class="row" data-id="${esc(g.id)}">
    ${g.art ? `<img loading="lazy" decoding="async" src="${esc(img(g.art, 120))}" alt="">` : '<div class="noimg"></div>'}
    <div class="t"><div class="name">${esc(g.title)}</div><div class="sub">${sub}</div></div>
    <div class="score"><div class="pct ${scoreClass(p)}">${p != null ? p + "%" : "—"}</div><div class="lbl">${esc(p != null ? g.label : reason)}</div></div>
    <div class="revs">${g.reviews ? fmtN(g.reviews) + "<br>reviews" : ""}</div>
    ${g.metascore ? `<div class="meta ${mcClass(g.metascore)}">${g.metascore}</div>` : '<div class="meta none">–</div>'}
  </div>`;
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
  const reason = unratedReason(g);
  const showSteamName = g.steam_name && norm(g.steam_name) !== norm(g.title);
  const genres = g.genres;
  const others = g.tags.filter(t => !genres.includes(t)).slice(0, 8);
  els.detail.innerHTML = `
    ${g.art ? `<img class="cover" src="${esc(img(g.art, 480))}" alt="">` : ""}
    <h2>${esc(g.title)}</h2>
    <div class="matched">${g.appid ? (showSteamName ? "Steam match: " + esc(g.steam_name) : "Steam app " + g.appid) + (g.override ? " (manual)" : "") : esc(reason)}</div>
    <div class="bigscore">
      <div><div class="pct ${scoreClass(p)}">${p != null ? p + "%" : "—"}</div></div>
      <div><div class="lbl ${scoreClass(p)}">${esc(p != null ? g.label : reason)}</div>
        <div class="n">${g.reviews ? g.reviews.toLocaleString() + " Steam reviews" : ""}</div>
        <div class="n">${g.rating != null ? "Weighted rating " + g.rating.toFixed(1) : ""}</div></div>
      ${g.metascore ? `<div class="meta ${mcClass(g.metascore)}" title="Metascore">${g.metascore}</div>` : ""}
    </div>
    <div class="facts">${g.year ? `Released <b>${g.year}</b>` : ""}${g.year && g.devs?.length ? " · " : ""}${g.devs?.length ? `<b>${esc(g.devs.slice(0, 2).join(", "))}</b>` : ""}</div>
    <div class="tags">${genres.map(t => `<span class="tag g">${esc(t)}</span>`).join("")}${others.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>
    ${g.desc ? `<p>${esc(g.desc)}</p>` : ""}
    <div class="state">${g.installed ? '<span class="inst">● Installed</span>' : "Not installed"} · ${esc(g.store)}</div>`;
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
    $("progressText").textContent = `${PHASE_TEXT[p.phase]}${p.total ? ` ${p.done}/${p.total}` : ""}${p.phase === "matching" && p.message ? " — " + p.message : ""}`;
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
    <div class="opts">${items.map((it, i) => `<div class="opt ${i === m.sel ? "sel" : ""} ${it.on ? "on" : ""}" data-i="${i}">
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
      { label: "Swap Menu / View buttons", value: swapLabel(), change: d => { S.swapMenu = cycle(["auto", "on", "off"], S.swapMenu, d); savePrefs(); } },
      { label: "Search…", activate: () => { closeModal(); focusSearch(); } },
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
        <div class="note">Heroic will open and ask you to confirm. Tap <b>Yes</b> using the touchscreen or right trackpad,
        then choose the install options in Heroic. Keep Heroic running until the download finishes.</div>`,
      onYes: async () => {
        const r = await api("/api/install", { id: g.id });
        toast(r.ok ? `Opening Heroic to install ${g.title}…` : "Couldn't open Heroic: " + r.error, 6000);
      },
    });
    return;
  }
  openModal({
    kind: "confirm", yes: "Launch", html: `Launch <b>${esc(g.title)}</b>?`,
    onYes: async () => {
      const r = await api("/api/launch", { id: g.id });
      toast(r.ok ? `Launching ${g.title} via Heroic…` : "Launch failed: " + r.error, 5000);
    },
  });
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
// The Steam Deck's own controller reports View (two squares) and Menu (three lines)
// the other way round from the standard layout, so swap them for Valve pads.
const isValvePad = p => /28de|valve|steam/i.test(p.id || "");
function padSwapped(p) {
  return S.swapMenu === "on" || (S.swapMenu === "auto" && isValvePad(p));
}
function swapLabel() {
  if (S.swapMenu !== "auto") return S.swapMenu === "on" ? "On" : "Off";
  const pads = navigator.getGamepads ? [...navigator.getGamepads()].filter(Boolean) : [];
  return `Auto (${pads.some(isValvePad) ? "on" : "off"})`;
}
function readPads() {
  const st = {};
  const pads = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const p of pads) {
    if (!p || !p.connected) continue;
    const swap = padSwapped(p);
    for (const [k, i0] of Object.entries(PAD)) {
      const i = swap && i0 === 8 ? 9 : swap && i0 === 9 ? 8 : i0;
      const b = p.buttons[i];
      if (b && (b.pressed || b.value > 0.5)) st[k] = true;
    }
    const ax = p.axes[0] || 0, ay = p.axes[1] || 0;
    if (ay < -0.55) st.up = true;
    if (ay > 0.55) st.down = true;
    if (ax < -0.7) st.left = true;
    if (ax > 0.7) st.right = true;
    const ry = p.axes[3] || 0;  // right stick scrolls the details panel
    if (Math.abs(ry) > 0.25) els.detail.scrollTop += ry * 14;
  }
  return st;
}
// Never let an error in one action kill controller input for good.
function safeInput(a) {
  try { input(a); } catch (e) { console.error(e); toast("Error: " + e.message, 6000); }
}
function padLoop(now) {
  requestAnimationFrame(padLoop);  // schedule first, so an exception can't stop the loop
  const st = readPads();
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

// Keyboard (also what Steam Input's keyboard templates send)
const KEYS = {
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "a", " ": "a",
  Escape: "b", Backspace: "b", x: "x", s: "x", y: "y", g: "y", q: "lb", "[": "lb", e: "rb", "]": "rb",
  PageUp: "lt", PageDown: "rt", r: "select", m: "start", Tab: "start", Home: "home", End: "end",
};
document.addEventListener("keydown", e => {
  if (document.activeElement === els.search) {
    if (["Escape", "Enter", "ArrowDown", "Tab"].includes(e.key)) { e.preventDefault(); els.search.blur(); }
    return;
  }
  if (e.key === "/") { e.preventDefault(); focusSearch(); return; }
  const a = KEYS[e.key];
  if (a) { e.preventDefault(); safeInput(a); }
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
