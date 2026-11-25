"use strict";

// ---------------------------------------------------------------- config
const SORTS = [
  { key: "rating", label: "Rating", dir: -1, get: g => g.rating },
  { key: "percent", label: "% Positive", dir: -1, get: g => g.percent },
  { key: "reviews", label: "Popularity", dir: -1, get: g => g.reviews || null },
  { key: "metascore", label: "Critic score", dir: -1, get: g => g.critic_score ?? g.metascore },   // Metascore, else IGDB critics
  { key: "year", label: "Release year", dir: -1, get: g => g.year },
  { key: "title", label: "Title", dir: 1, get: g => g.title.toLowerCase() },
];
const STORES = ["All", "Epic", "GOG", "Amazon", "Other"];
const PHASE_TEXT = { starting: "Reading Heroic library…", matching: "Matching titles on Steam", wikidata: "Looking up the rest on Wikidata", ratings: "Fetching Steam reviews", igdb: "IGDB" };
// IGDB play styles (game modes) and perspectives, for the filters. Only with an IGDB (Twitch) key.
const STYLES = { "Any": null, "Single player": "Single player", "Co-op": "Co-operative", "Split screen": "Split screen",
                 "Multiplayer": "Multiplayer", "MMO": "Massively Multiplayer Online (MMO)", "Battle royale": "Battle Royale" };
const PERSPECTIVES = { "Any": null, "First person": "First person", "Third person": "Third person",
                       "Isometric / top-down": "Bird view / Isometric", "Side view": "Side view", "VR": "Virtual Reality", "Text": "Text" };

// ---------------------------------------------------------------- state
const S = {
  games: [], view: [], sel: 0, version: -1, progress: {},
  sortIdx: 0, dir: -1, genre: null, store: "All", installedOnly: false, hideUnrated: false, style: "Any", persp: "Any", trailerSound: false, query: "",
  modal: null,
};
const $ = id => document.getElementById(id);
const els = { list: $("list"), detail: $("detail"), search: $("search"), overlay: $("overlay"), modal: $("modal") };

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem("hr-prefs") || "{}");
    for (const k of ["sortIdx", "dir", "genre", "store", "installedOnly", "hideUnrated", "style", "persp", "trailerSound"]) if (k in p) S[k] = p[k];
    if (!(S.style in STYLES)) S.style = "Any";
    if (!(S.persp in PERSPECTIVES)) S.persp = "Any";
    if (!SORTS[S.sortIdx]) S.sortIdx = 0;
  } catch (e) { /* storage unavailable */ }
}
function savePrefs() {
  try {
    const { sortIdx, dir, genre, store, installedOnly, hideUnrated, style, persp, trailerSound } = S;
    localStorage.setItem("hr-prefs", JSON.stringify({ sortIdx, dir, genre, store, installedOnly, hideUnrated, style, persp, trailerSound }));
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
  if (g.critic != null) return { big: String(g.critic), cls: scoreClass(g.critic), label: critLabel(g) };
  const ig = igdbScore(g);
  if (ig) return ig;
  return { big: "—", cls: "c-na", label: unratedReason(g) };
}
// The chosen critic score's source and review count, e.g. "Critics · OpenCritic · 33".
const critLabel = g => `Critics · ${g.critic_by}${g.critic_n ? " · " + g.critic_n : ""}`;
// Last resort for games with no Steam reviews and no critic score: IGDB's user rating.
function igdbScore(g) {
  if (g.rating_src === "igdb-user") return { big: String(g.igdb.user), cls: scoreClass(g.igdb.user), label: `IGDB users · ${g.igdb.user_n}` };
  return null;
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
    (!S.hideUnrated || g.rating != null || g.percent != null) &&
    (!STYLES[S.style] || (g.modes || []).includes(STYLES[S.style])) &&
    (!PERSPECTIVES[S.persp] || (g.persp || []).includes(PERSPECTIVES[S.persp])) &&
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
  const badge = dl ? `<span class="badge dl">${esc(dl)}</span>` : g.installed ? '<span class="badge inst">Installed</span>' : "";
  return `<div class="row" data-id="${esc(g.id)}">
    ${g.art ? `<img loading="lazy" decoding="async" src="${esc(img(g.art, 120))}" alt="">` : '<div class="noimg"></div>'}
    <div class="t"><div class="name">${esc(g.title)}</div>${badge}</div>
    <div class="score"></div>
  </div>`;
}

// How long to beat (IGDB): "29h", "4½h", "190h".
const fmtHours = n => n == null ? "—" : n >= 100 ? Math.round(n) + "h" : n < 1 ? Math.max(1, Math.round(n * 60)) + "m"
  : (Math.round(n * 2) / 2).toString().replace(".5", "½") + "h";

// The one value shown in the list: whatever the current sort orders by.
const mcScoreClass = m => m >= 75 ? "c-pos" : m >= 50 ? "c-mix" : "c-neg";
function sortValue(g, key = SORTS[S.sortIdx].key) {
  const none = label => ({ big: "—", cls: "c-na", label });
  switch (key) {
    case "percent":
      if (g.percent != null) return { big: g.percent + "%", cls: scoreClass(g.percent), label: g.label };
      return none(g.critic != null ? "No Steam reviews" : unratedReason(g));
    case "reviews":
      if (g.reviews) return { big: fmtN(g.reviews), cls: "c-plain", label: g.reviews === 1 ? "review" : "reviews" };
      return none(g.critic != null ? "No Steam reviews" : unratedReason(g));
    case "metascore": {
      const c = g.critic_score ?? g.metascore;
      return c ? { big: String(c), cls: mcScoreClass(c), label: g.critic_src || "Metascore" } : none("No critic score");
    }
    case "year":
      return g.year ? { big: String(g.year), cls: "c-plain", label: "Released" } : none("Release date unknown");
    default: // Rating (and Title, which has no number of its own)
      if (g.percent != null && g.rating != null) return { big: Math.round(g.rating), cls: scoreClass(g.rating), label: g.label };
      if (g.critic != null) return { big: String(g.critic), cls: scoreClass(g.critic), label: critLabel(g) };
      return igdbScore(g) || none(unratedReason(g));
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
  if (S.style !== "Any") f.push(S.style);
  if (S.persp !== "Any") f.push(S.persp);
  $("chipFilters").textContent = f.join(" · ");
  $("count").textContent = `${S.view.length} / ${S.games.length} games`;

  if (!S.view.length) {
    const msg = !S.games.length
      ? (S.progress.phase === "error" ? esc(S.progress.message) : "Loading your Heroic library…")
      : "No games match these filters.<br>Change them in the menu, or pick another genre with <span class='btn b'>B</span>.";
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

// ---------------------------------------------------------------- videos
// Steam's store videos (HLS) and IGDB's YouTube videos, fetched once a game has been
// selected for a moment, and played in the details pane: muted and looping by default.
// R3 / v toggles sound (remembered), right stick ◀ ▶ switches video, tapping a thumbnail
// plays that one.
S.videos = {};            // game id -> [{src, name, thumb, url | id}]
let videoTimer = null;
let inline = null;        // { gid, i, hls }
let hlsLoading = null;
function loadHls() {
  if (window.Hls) return Promise.resolve();
  return hlsLoading ||= new Promise((ok, fail) => {
    const sc = document.createElement("script");
    sc.src = "vendor/hls.min.js"; sc.onload = ok; sc.onerror = fail;
    document.head.appendChild(sc);
  });
}
async function loadVideos(g) {
  if (S.videos[g.id]) return S.videos[g.id];
  try { S.videos[g.id] = (await api("/api/videos", { id: g.id })).videos || []; } catch { return []; }
  return S.videos[g.id];
}
function queueVideos(g) {   // only once the selection has rested for a moment
  clearTimeout(videoTimer);
  stopInline();
  if (!g) return;
  videoTimer = setTimeout(async () => {
    await loadVideos(g);
    if (S.view[S.sel]?.id !== g.id) return;
    renderVideoStrip(g);
    playInline(g, 0);
  }, 600);
}
function stopInline() {
  if (inline?.hls) inline.hls.destroy();
  inline = null;
  const box = document.getElementById("vbox");
  if (box) box.querySelectorAll("video, iframe").forEach(el => { if (el.pause) el.pause(); el.remove(); });
}
async function playInline(g, i) {
  const vs = S.videos[g.id] || [];
  const box = document.getElementById("vbox");
  if (!vs.length || !box || box.dataset.id !== g.id) return;
  stopInline();
  i = (i + vs.length) % vs.length;
  const v = vs[i], me = { gid: g.id, i, hls: null };
  inline = me;
  box.classList.add("playing");
  box.querySelector(".cap").innerHTML = `${esc(v.name)} · ${i + 1}/${vs.length} · <span class="btn sh">R3</span> ${S.trailerSound ? "Mute" : "Sound"}`;
  markCarousel(i, vs.length);
  if (v.src === "youtube") {
    const f = document.createElement("iframe");
    f.allow = "autoplay; encrypted-media";
    f.src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.id)}?autoplay=1&mute=${S.trailerSound ? 0 : 1}` +
            `&loop=1&playlist=${encodeURIComponent(v.id)}&controls=0&enablejsapi=1&playsinline=1&rel=0&origin=${encodeURIComponent(location.origin)}`;
    box.prepend(f);
    return;
  }
  const video = document.createElement("video");
  Object.assign(video, { muted: !S.trailerSound, loop: true, playsInline: true, autoplay: true });
  box.prepend(video);
  if (video.canPlayType("application/vnd.apple.mpegurl")) video.src = v.url;
  else {
    try { await loadHls(); } catch { return; }
    if (inline !== me) return;
    me.hls = new Hls({ capLevelToPlayerSize: true });
    me.hls.loadSource(v.url);
    me.hls.attachMedia(video);
  }
  if (!paused && document.hasFocus()) video.play().catch(() => {});
}
function inlineVideo() { return document.querySelector("#vbox video"); }
function toggleSound() {
  S.trailerSound = !S.trailerSound; savePrefs();
  const v = inlineVideo(), f = document.querySelector("#vbox iframe");
  if (v) v.muted = !S.trailerSound;
  if (f) f.contentWindow?.postMessage(JSON.stringify({ event: "command", func: S.trailerSound ? "unMute" : "mute", args: [] }), "*");
  const cap = document.querySelector("#vbox .cap .btn");
  if (cap) cap.nextSibling.textContent = S.trailerSound ? " Mute" : " Sound";
  toast(S.trailerSound ? "Trailer sound on" : "Trailer sound off", 1200);
}
let lastStep = 0;
function stepVideo(d) {
  const g = S.view[S.sel], now = performance.now();
  if (now - lastStep < 350) return;   // holding ◀ ▶ shouldn't flick through videos
  lastStep = now;
  if (g && inline?.gid === g.id) playInline(g, inline.i + d);
}
function pauseInline(on) {   // while Heroic or a game is in front, or the app isn't focused
  const v = inlineVideo(), f = document.querySelector("#vbox iframe");
  if (v) on ? v.pause() : v.play().catch(() => {});
  if (f) f.contentWindow?.postMessage(JSON.stringify({ event: "command", func: on ? "pauseVideo" : "playVideo", args: [] }), "*");
}
window.addEventListener("blur", () => pauseInline(true));
window.addEventListener("focus", () => { if (!paused) pauseInline(false); });
// The thumbnail strip is a carousel: no scrollbar, it slides to keep the playing video
// centred, and fades with ‹ › show when there are more thumbnails off to a side.
function markCarousel(i, n) {
  const strip = document.querySelector("#vstrip .vthumbs");
  if (!strip) return;
  const thumbs = strip.querySelectorAll(".vthumb");
  thumbs.forEach((t, k) => t.classList.toggle("on", k === i));
  const t = thumbs[i];
  if (t) strip.scrollTo({ left: t.offsetLeft - (strip.clientWidth - t.offsetWidth) / 2, behavior: "smooth" });
  const pos = document.querySelector("#vstrip .vpos");
  if (pos) pos.textContent = `· ${i + 1} / ${n} · ◀ ▶ to switch`;
}
function updateCarouselEdges(strip) {
  const car = strip.parentElement;
  car.classList.toggle("more-l", strip.scrollLeft > 4);
  car.classList.toggle("more-r", strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 4);
}
function renderVideoStrip(g) {
  const el = document.getElementById("vstrip");
  const vs = S.videos[g.id] || [];
  if (!el || el.dataset.id !== g.id) return;
  el.innerHTML = vs.length > 1 ? `<div class="hl-title">Videos <span class="vpos">· ◀ ▶ to switch</span></div>
    <div class="vcar"><div class="vthumbs">${vs.map((v, i) =>
    `<div class="vthumb" data-i="${i}"><img loading="lazy" src="${esc(v.thumb)}" alt=""><span class="vsrc ${v.src}">${v.src === "steam" ? "Steam" : "YouTube"}</span><span class="vname">${esc(v.name)}</span></div>`).join("")}</div>
    <span class="varrow l" data-d="-1">‹</span><span class="varrow r" data-d="1">›</span></div>` : "";
  const strip = el.querySelector(".vthumbs");
  if (strip) {
    strip.addEventListener("scroll", () => updateCarouselEdges(strip), { passive: true });
    updateCarouselEdges(strip);
    if (inline?.gid === g.id) markCarousel(inline.i, vs.length);
  }
  if (!vs.length) {
    const cap = document.querySelector("#vbox .cap");
    if (cap) cap.textContent = "No videos";
  }
}
els.detail.addEventListener("click", e => {
  const g = S.view[S.sel];
  const t = e.target.closest(".vthumb");
  if (t && g) return playInline(g, +t.dataset.i);
  const arrow = e.target.closest(".varrow");
  if (arrow) { lastStep = 0; return stepVideo(+arrow.dataset.d); }
  if (e.target.closest("#vbox")) {   // tap the video: play / pause
    const v = inlineVideo();
    if (v) v.paused ? v.play().catch(() => {}) : v.pause();
  }
});

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
  const same = inline?.gid === S.view[i].id;
  renderDetail(S.view[i]);
  if (!same) queueVideos(S.view[i]);   // after renderDetail, which clears the old trailer
}

// The details pane: the trailer (top left) with the title and four stat tiles beside it
// (the first tile is the value the list is sorted by, so the two always agree), and
// everything else below.
function styleTile(g) {
  const m = g.modes || [];
  const v = m.includes("Co-operative") ? "Co-op" : m.includes("Split screen") ? "Split screen"
    : m.includes("Multiplayer") && !m.includes("Single player") ? "Multiplayer" : m.includes("Single player") ? "Single player" : null;
  return v && { big: v, cls: "c-plain small", label: "Play style" };
}
// The score tiles. Each kind of score appears once, from the source in use, with its
// review count: user score = Steam % (or IGDB users for games without Steam reviews),
// critic score = the one the app chose (Metascore, IGDB or OpenCritic).
const fmtCount = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e4 ? Math.round(n / 1e3) + "k" : (n || 0).toLocaleString();
function scoreTile(g, key) {
  switch (key) {
    case "rating":
      if (g.percent != null && g.rating != null) return { big: Math.round(g.rating), cls: scoreClass(g.rating), label: "Rating" };
      if (g.critic != null) return { big: g.critic, cls: scoreClass(g.critic), label: `Rating · ${g.critic_by}${g.critic_n ? ` · ${fmtCount(g.critic_n)} reviews` : ""}` };
      if (g.rating_src === "igdb-user") return { big: g.igdb.user, cls: scoreClass(g.igdb.user), label: `Rating · IGDB users · ${fmtCount(g.igdb.user_n)}` };
      return null;
    case "percent":
      if (g.percent != null) return { big: g.percent + "%", cls: scoreClass(g.percent), label: `Steam · ${fmtCount(g.reviews)} reviews` };
      if (g.igdb?.user && g.rating_src !== "igdb-user") return { big: g.igdb.user, cls: scoreClass(g.igdb.user), label: `IGDB users · ${fmtCount(g.igdb.user_n)} ratings` };
      return null;
    case "metascore": {
      const c = g.critic_score ?? g.metascore;
      if (!c || (g.percent == null && g.rating_src === "critic" && c === g.critic)) return null;   // already the rating
      return { big: c, cls: mcScoreClass(c), label: g.critic_src && g.critic_src !== "Metascore" ? `${g.critic_by} · ${fmtCount(g.critic_n)} reviews` : "Metascore" };
    }
    case "reviews": return g.reviews ? { big: fmtCount(g.reviews), cls: "c-plain", label: "Steam reviews" } : null;
    case "year": return g.year ? { big: g.year, cls: "c-plain", label: "Released" } : null;
    case "style": return styleTile(g);
  }
  return null;
}
function detailTiles(g) {
  const cur = SORTS[S.sortIdx].key === "title" ? "rating" : SORTS[S.sortIdx].key;
  // first tile = exactly what the list shows for this sort (richer label when it's the same value)
  const sv = sortValue(g, cur), st = scoreTile(g, cur);
  const same = st && String(st.big) === String(sv.big);
  const tiles = [{ ...(same ? st : sv), cur: true }];
  // a game without Steam reviews is rated by its critic score: don't show that score twice
  const ratingIsCritic = g.percent == null && g.rating_src === "critic";
  for (const k of ["rating", "percent", "metascore", "style", "year"]) {
    if (tiles.length >= 4) break;
    if (k === cur && same) continue;
    if (k === "rating" && cur === "metascore" && ratingIsCritic) continue;
    const t = scoreTile(g, k);
    if (t) tiles.push(t);
  }
  return tiles;
}

// Where the Steam match came from, only when that's worth knowing (it helps spot a wrong match).
function matchNote(g, showSteamName) {
  if (!g.appid) return g.rating == null && g.percent == null ? `<div class="matched">${esc(unratedReason(g) || "Not on Steam")}</div>` : "";
  const via = g.via === "wikidata" ? " (found via Wikidata)" : g.via === "igdb" ? " (found via IGDB)" : "";
  if (!showSteamName && !via && !g.override) return "";
  return `<div class="matched">Steam match: ${esc(g.steam_name || "app " + g.appid)}${via}${g.override ? " (manual)" : ""}</div>`;
}

// How long to beat (IGDB), as a row of three small tiles under the score tiles.
function lengthsHTML(g) {
  const L = g.length;
  if (!document.body.classList.contains("lengths") || !L) return "";
  return `<div class="lenhead">How long to beat</div><div class="lens">
    <div><b>${fmtHours(L.main)}</b><small>Main story</small></div>
    <div><b>${fmtHours(L.extra)}</b><small>+ Extras</small></div>
    <div><b>${fmtHours(L.comp)}</b><small>100%</small></div></div>`;
}
function renderDetail(g) {
  // Same game already showing with its trailer playing (e.g. the list reloaded during a
  // sync): update everything but the video, so it doesn't restart.
  const keep = inline?.gid === g.id && document.getElementById("vbox")?.dataset.id === g.id;
  if (!keep) stopInline();
  const tiles = detailTiles(g);
  const showSteamName = g.steam_name && norm(g.steam_name) !== norm(g.title);
  const status = downloadText(g, true) ? `<span class="dl">${esc(downloadText(g, true))}</span>` : g.installed ? '<span class="inst">● Installed</span>' : "Not installed";
  const html = `
    <div class="top">
      <div id="vbox" data-id="${esc(g.id)}">
        ${g.art ? `<img class="poster" src="${esc(img(g.art, 480))}" alt="">` : ""}
        <div class="cap">${S.videos[g.id] && !S.videos[g.id].length ? "No videos" : "Loading trailer…"}</div>
      </div>
      <div class="info">
        <h2>${esc(g.title)}</h2>
        <div class="sub">${[g.year, g.devs?.[0]].filter(Boolean).map(esc).join(" · ")}</div>
        <div class="tiles">${tiles.map(t => `<div class="${t.cur ? "cur" : ""}"><b class="${t.cls}">${esc(String(t.big))}</b><small>${esc(t.label || "")}</small></div>`).join("")}</div>
        ${lengthsHTML(g)}
        <div class="state">${status} · ${esc(g.store)}</div>
      </div>
    </div>
    ${g.desc ? `<p class="desc">${esc(g.desc)}</p>` : ""}
    ${matchNote(g, showSteamName)}
    <div id="vstrip" data-id="${esc(g.id)}"></div>
    ${igdbDetailHTML(g)}
    ${g.genres.length ? `<div class="tags">${g.genres.map(t => `<span class="tag g">${esc(t)}</span>`).join("")}</div>` : ""}`;
  if (keep) {
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    els.detail.querySelector(".top .info").replaceWith(tmp.querySelector(".top .info"));
    const top = els.detail.querySelector(".top");
    while (top.nextSibling) top.nextSibling.remove();
    const rest = document.createDocumentFragment();
    for (let n = tmp.querySelector(".top").nextSibling; n; ) { const next = n.nextSibling; rest.appendChild(n); n = next; }
    els.detail.appendChild(rest);
    renderVideoStrip(g);
    return;
  }
  els.detail.innerHTML = html;
  els.detail.scrollTop = 0;
  if (S.videos[g.id]) renderVideoStrip(g);
}

// IGDB extras in the details panel: its scores, play styles, multiplayer, series, similar games.
function mpText(mp) {
  const parts = [];
  if (mp.campaigncoop) parts.push("Co-op campaign");
  if (mp.offlinecoopmax) parts.push(`Local co-op up to ${mp.offlinecoopmax}`); else if (mp.offlinecoop) parts.push("Local co-op");
  if (mp.onlinecoopmax) parts.push(`Online co-op up to ${mp.onlinecoopmax}`); else if (mp.onlinecoop) parts.push("Online co-op");
  if (mp.splitscreen) parts.push("Split screen");
  if (mp.lancoop) parts.push("LAN co-op");
  if (mp.onlinemax && !mp.onlinecoopmax) parts.push(`Online up to ${mp.onlinemax}`);
  return parts.join(" · ");
}
function gameLinks(ids) {
  return ids.map(id => S.games.find(x => x.id === id)).filter(Boolean)
    .map(x => `<span class="glink" data-id="${esc(x.id)}">${esc(x.title)}</span>`).join("");
}
function igdbDetailHTML(g) {
  const ig = g.igdb;
  if (!ig) return "";
  const style = [...(g.modes || []).map(m => m.replace("Massively Multiplayer Online (MMO)", "MMO").replace("Co-operative", "Co-op")),
                 ...(g.persp || []).map(p => p.replace("Bird view / Isometric", "Isometric"))];
  const mp = mpText(ig.mp || {});
  const series = ig.same_series?.length ? gameLinks(ig.same_series) : "";
  const similar = ig.similar?.length ? gameLinks(ig.similar) : "";
  return `
    ${style.length ? `<div class="ig-line">${esc(style.join(" · "))}</div>` : ""}
    ${mp ? `<div class="ig-line">${esc(mp)}</div>` : ""}
    ${series ? `<div class="hl-title">Same series in your library${ig.series[0] ? ` <span>· ${esc(ig.series[0])}</span>` : ""}</div><div class="glinks">${series}</div>` : ""}
    ${similar ? `<div class="hl-title">More like this in your library</div><div class="glinks">${similar}</div>` : ""}`;
}
els.detail.addEventListener("click", e => {
  const l = e.target.closest(".glink");
  if (!l) return;
  let i = S.view.findIndex(g => g.id === l.dataset.id);
  if (i < 0) {   // filtered out: clear filters so it can be shown
    S.query = ""; els.search.value = ""; S.genre = null; S.store = "All"; S.installedOnly = false; S.hideUnrated = false;
    S.style = "Any"; S.persp = "Any"; savePrefs(); buildView(l.dataset.id);
    i = S.view.findIndex(g => g.id === l.dataset.id);
  }
  select(i);
});

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
  if (m.kind === "keyboard") { els.modal.innerHTML = kbHTML(m); return; }
  if (m.kind === "prompt") {   // one text field (PC: type with the keyboard)
    els.modal.innerHTML = `<h3>${esc(m.title)}</h3>
      <div class="q">${m.html || ""}</div>
      <input id="promptInput" type="${m.secret ? "password" : "text"}" autocomplete="off" spellcheck="false" value="${esc(m.value || "")}">
      <div class="hint"><span>Enter OK</span><span>Esc Cancel</span></div>`;
    const inp = document.getElementById("promptInput");
    setTimeout(() => { inp.focus(); inp.select(); }, 0);
    return;
  }

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
  if (m.kind === "keyboard") return kbInput(m, a);
  if (m.kind === "prompt") {
    if (a === "a") { const v = document.getElementById("promptInput").value; closeModal(); m.onSave(v); }
    else if (a === "b" || a === "start") { closeModal(); m.onCancel?.(); }
    return;
  }

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
    case "home": m.sel = 0; break;                          // hold LT
    case "end": m.sel = items.length - 1; break;            // hold RT
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

async function rematchUnmatched() {
  closeModal();
  const r = await api("/api/rematch-unmatched", {});
  toast(r.count ? `Looking for ${r.count} unmatched game${r.count === 1 ? "" : "s"} on Steam, Wikidata${document.body.classList.contains("lengths") ? " and IGDB" : ""}…` : "No unmatched games", 4000);
}
function openMenu() {
  const g = S.view[S.sel];
  const unmatched = S.games.filter(x => !x.appid && !x.extra && !x.override).length;
  const storesPresent = STORES.filter(s => s === "All" || S.games.some(x => x.store === s));
  const refilter = () => { savePrefs(); buildView(S.view[S.sel]?.id); };
  openModal({
    title: "Menu",
    hint: `<span>◀ ▶ change</span><span><span class="btn a">A</span>Select</span><span><span class="btn b">B</span>Close</span>`,
    items: () => [
      { label: "Store", value: S.store, change: d => { S.store = cycle(storesPresent, S.store, d); refilter(); } },
      { label: "Installed only", value: S.installedOnly ? "On" : "Off", change: () => { S.installedOnly = !S.installedOnly; refilter(); } },
      { label: "Hide unrated games", value: S.hideUnrated ? "On" : "Off", change: () => { S.hideUnrated = !S.hideUnrated; refilter(); } },
      ...(document.body.classList.contains("lengths") ? [
        { label: "Play style", value: S.style, change: d => { S.style = cycle(Object.keys(STYLES), S.style, d); refilter(); } },
        { label: "Perspective", value: S.persp, change: d => { S.persp = cycle(Object.keys(PERSPECTIVES), S.persp, d); refilter(); } },
      ] : []),
      ...(unmatched ? [{ label: `Match unmatched games (${unmatched})`, activate: rematchUnmatched }] : []),
      { label: "Settings…", activate: () => openSettings() },
      ...(g && g.installed && !S.downloads[g.id] ? [{ label: `Uninstall “${g.title}”`, activate: () => { closeModal(); uninstallFlow(g); } }] : []),
      ...(g && !g.extra ? [{ label: `Steam match for “${g.title}”…`, value: g.appid ? (g.override ? "set by you" : "app " + g.appid) : "none",
                             activate: () => matchFlow(g, g.title) }] : []),
      { label: "Refresh library & new ratings", activate: () => { api("/api/refresh", {}); closeModal(); toast("Refreshing…"); } },
      { label: "Re-fetch all ratings", activate: () => { api("/api/refresh", { force: true }); closeModal(); toast("Re-fetching everything from Steam…"); } },
      { label: "Quit", activate: quit },
    ],
  });
}

// ---------------------------------------------------------------- settings
// Twitch/IGDB credentials. The secret never comes back from the backend; values typed
// here are only sent when saved, after Twitch has accepted them.
const settingsDraft = { client_id: "", client_secret: "" };
async function openSettings(sel = 0) {
  let st;
  try { st = (await api("/api/settings", {})).twitch; } catch { toast("Couldn't load settings", 3000); return; }
  const fromEnv = st.source === "env";
  const draftSet = settingsDraft.client_id || settingsDraft.client_secret;
  const items = () => [
    { info: true, label: "IGDB (optional): critic scores for more games, how long to beat, play styles, series and gameplay videos." },
    { label: "Status", value: fromEnv ? "Set by environment variables" : st.set ? `Connected · ${st.client_id_hint}` : "Not set", info: true },
    ...(fromEnv ? [] : [
      { label: "Twitch client ID", value: settingsDraft.client_id ? settingsDraft.client_id : st.set ? st.client_id_hint : "Not set",
        activate: () => editSetting("client_id", "Twitch client ID", false) },
      { label: "Twitch client secret", value: settingsDraft.client_secret ? "•".repeat(10) + " (new)" : st.set ? "•".repeat(10) : "Not set",
        activate: () => editSetting("client_secret", "Twitch client secret", true) },
      ...(draftSet ? [{ label: "Save and check with Twitch", activate: saveTwitch }] : []),
      ...(st.set ? [{ label: "Remove credentials", activate: removeTwitch }] : []),
    ]),
    { info: true, label: "Get them free at dev.twitch.tv/console → Register Your Application (OAuth redirect URL http://localhost, client type Confidential) → Client ID, then New Secret." },
  ];
  if (!sel) sel = items().findIndex(i => !i.info);   // start on the first thing you can change
  openModal({ kind: "list settings", title: "Settings", hint: "A select · B close", items, sel });
}
function editSetting(key, title, secret) {
  const back = S.modal.sel;
  askText({ title, secret, value: settingsDraft[key],
    html: secret ? "Paste or type the secret. It's stored only on this device." : "",
    onDone: v => { settingsDraft[key] = v.trim(); openSettings(back); },
    onCancel: () => openSettings(back) });
}
async function saveTwitch() {
  if (!settingsDraft.client_id || !settingsDraft.client_secret) { toast("Enter both the client ID and the secret", 3000); return; }
  toast("Checking with Twitch…", 2000);
  let r;
  try { r = await api("/api/settings/twitch", settingsDraft); } catch { r = { ok: false, error: "No reply from the app" }; }
  if (r.ok) {
    settingsDraft.client_id = settingsDraft.client_secret = "";
    closeModal();
    toast("Twitch accepted them. Fetching IGDB data…", 4000);
  } else {
    openModal({ kind: "confirm", yes: "OK", html: esc(r.error), onYes: () => openSettings() });
  }
}
async function removeTwitch() {
  const r = await api("/api/settings/twitch", { remove: true });
  if (r.ok) { toast("Credentials removed", 2500); openSettings(); }
  else openModal({ kind: "confirm", yes: "OK", html: esc(r.error), onYes: () => openSettings() });
}

// Typing text: the on-screen keyboard on a Steam Deck, a text box on a PC.
function askText({ title, value = "", secret = false, html = "", onDone, onCancel }) {
  if (S.steamDeck) openKeyboard({ title, value, secret, html, onDone, onCancel });
  else openModal({ kind: "prompt", title, secret, value, html, onSave: onDone, onCancel });
}

// Menu → Steam match: pick the right Steam page for a game (or none). Results come from
// Steam's store search and IGDB (games listing a Steam page); a number is a Steam app ID.
function matchFlow(g, query) {
  askText({ title: `Steam match for ${g.title}`, value: query,
    html: "Search by name, or type the Steam app ID (the number in the game's Steam store address).",
    onDone: q => q.trim() && matchResults(g, q.trim()) });
}
async function matchResults(g, q) {
  toast("Searching…", 1500);
  let r;
  try { r = await api("/api/match-search", { id: g.id, query: q }); } catch { r = { results: [] }; }
  const res = r.results || [];
  const items = () => [
    ...(res.length ? [] : [{ info: true, label: `Nothing found for “${q}”. Try another name, or type the Steam app ID.` }]),
    ...res.map(x => ({ label: x.name, value: [x.year, x.src, "app " + x.appid].filter(Boolean).join(" · "),
                       on: x.appid === g.appid, activate: () => setMatch(g, x.appid, x.name) })),
    { label: "Search again…", activate: () => matchFlow(g, q) },
    ...(g.appid ? [{ label: "Not on Steam (remove the match)", activate: () => setMatch(g, null) }] : []),
  ];
  openModal({ kind: "list", title: `Steam match for ${g.title}`, hint: "A pick · B cancel", items, sel: res.length ? 0 : items().length - 1 });
}
async function setMatch(g, appid, name) {
  closeModal();
  await api("/api/override", { id: g.id, appid });
  toast(appid ? `Matched to ${name}. Fetching its reviews…` : "Match removed", 3500);
  setTimeout(loadGames, 600);
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
    "heroic-busy": `Heroic is still busy (downloading or uninstalling), so ${title} can't be launched yet.
      <div class="note">Launching closes the Heroic this app runs in the background, which would stop that. Try again when it's finished.</div>`,
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

// ---------------------------------------------------------------- on-screen keyboard
// D-pad moves, A types, X deletes, Y space, LB capitals, Menu done, B cancel.
const KB_ROWS = [
  [..."1234567890"], [..."qwertyuiop"], [..."asdfghjkl'"], [..."zxcvbnm,.-"],
  ["⇧ Caps", "Space", "⌫ Delete", "✓ Done"],
];
function openKeyboard(opts) {
  openModal({ kind: "keyboard", r: 1, c: 0, caps: false, value: opts.value || "", ...opts });
}
function kbKey(m, r = m.r, c = m.c) { return KB_ROWS[r][Math.min(c, KB_ROWS[r].length - 1)]; }
function kbPress(m, key) {
  if (key === "⇧ Caps") m.caps = !m.caps;
  else if (key === "Space") m.value += " ";
  else if (key === "⌫ Delete") m.value = m.value.slice(0, -1);
  else if (key === "✓ Done") { closeModal(); m.onDone?.(m.value); return; }
  else m.value += m.caps ? key.toUpperCase() : key;
  m.onChange?.(m.value);
  if (S.modal === m) renderModal();
}
function kbInput(m, a) {
  const rows = KB_ROWS.length, wide = r => KB_ROWS[r].length;
  // moving between the 10-key rows and the 4-key bottom row keeps the position roughly
  const toRow = (r, nr) => { m.c = Math.round(m.c * (wide(nr) - 1) / Math.max(1, wide(r) - 1)); m.r = nr; };
  switch (a) {
    case "up": if (m.r > 0) toRow(m.r, m.r - 1); break;
    case "down": if (m.r < rows - 1) toRow(m.r, m.r + 1); break;
    case "left": m.c = (m.c - 1 + wide(m.r)) % wide(m.r); break;
    case "right": m.c = (m.c + 1) % wide(m.r); break;
    case "a": return kbPress(m, kbKey(m));
    case "x": return kbPress(m, "⌫ Delete");
    case "y": return kbPress(m, "Space");
    case "lb": return kbPress(m, "⇧ Caps");
    case "start": return kbPress(m, "✓ Done");
    case "b": closeModal(); m.onCancel?.(); return;
  }
  renderModal();
}
function kbHTML(m) {
  const shown = m.secret ? "•".repeat(m.value.length) : esc(m.value);
  return `<h3>${esc(m.title)}</h3>${m.html ? `<div class="kbhelp">${m.html}</div>` : ""}
    <div class="kbfield">${shown}<span class="caret"></span></div>
    <div class="kb">${KB_ROWS.map((row, r) => `<div class="kbrow">${row.map((k, c) =>
      `<span class="kkey ${r === m.r && c === Math.min(m.c, row.length - 1) ? "sel" : ""} ${k === "⇧ Caps" && m.caps ? "on" : ""} ${row.length < 10 ? "wide" : ""}"
             data-r="${r}" data-c="${c}">${esc(m.caps && k.length === 1 ? k.toUpperCase() : k)}</span>`).join("")}</div>`).join("")}</div>
    <div class="hint"><span><span class="btn a">A</span>Type</span><span><span class="btn x">X</span>Delete</span>
      <span><span class="btn y">Y</span>Space</span><span><span class="btn sh">LB</span>Caps</span>
      <span><span class="btn glyph"><svg viewBox="0 0 16 16"><path d="M3 4.5h10M3 8h10M3 11.5h10"/></svg></span>Done</span><span><span class="btn b">B</span>Cancel</span></div>`;
}
els.modal.addEventListener("click", e => {   // touch / mouse
  const k = e.target.closest(".kkey");
  if (k && S.modal?.kind === "keyboard") { S.modal.r = +k.dataset.r; S.modal.c = +k.dataset.c; kbPress(S.modal, kbKey(S.modal)); }
});

// ---------------------------------------------------------------- main input
function pageSize() {
  const row = els.list.querySelector(".row");
  return row ? Math.max(1, Math.floor(els.list.clientHeight / row.offsetHeight) - 1) : 8;
}

function input(a, repeat = false) {
  document.body.classList.add("nomouse");
  if (S.modal) return modalInput(a);
  if (document.activeElement === els.search) {
    if (["b", "a", "down", "start"].includes(a)) els.search.blur();
    return;
  }
  switch (a) {
    // at the ends a fresh press wraps round; holding (repeat) stops at the end
    case "up": select(S.sel === 0 ? (repeat ? 0 : S.view.length - 1) : S.sel - 1); break;
    case "down": select(S.sel === S.view.length - 1 ? (repeat ? S.sel : 0) : S.sel + 1); break;
    case "lt": select(S.sel - pageSize()); break;
    case "rt": select(S.sel + pageSize()); break;
    case "left": stepVideo(-1); break;     // D-pad ◀ ▶: previous / next video
    case "right": stepVideo(1); break;
    case "home": select(0); break;
    case "end": select(S.view.length - 1); break;
    case "a": launchSelected(); break;
    case "b": openGenres(); break;
    case "x": setSort((S.sortIdx + 1) % SORTS.length); toast("Sort: " + SORTS[S.sortIdx].label, 1200); break;
    case "select": S.dir = -S.dir; savePrefs(); buildView(S.view[S.sel]?.id); break;
    case "y": openSearch(); break;
    case "lb": cycleGenre(-1); break;
    case "rb": cycleGenre(1); break;
    case "start": openMenu(); break;
    case "r3": toggleSound(); break;
    case "vprev": stepVideo(-1); break;
    case "vnext": stepVideo(1); break;
  }
}

// Gamepad (standard mapping; Steam Input presents an Xbox-style pad)
const PAD = { a: 0, b: 1, x: 2, y: 3, lb: 4, rb: 5, lt: 6, rt: 7, select: 8, start: 9, r3: 11, up: 12, down: 13, left: 14, right: 15,
              vprev: -1, vnext: -1 };   // right stick ◀ ▶ (set from the axis in readPads)
const REPEAT = new Set(["up", "down", "left", "right"]);
const JUMP = { lt: "home", rt: "end" };   // tap LT/RT: page; hold half a second: top/bottom
const JUMP_HOLD_MS = 500;
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
    const ry = p.axes[3] || 0;  // right stick: up/down scrolls the details pane, left/right switches video
    if (scroll && Math.abs(ry) > 0.25) els.detail.scrollTop += ry * 14;
    const rx = p.axes[2] || 0;
    if (rx < -0.7 && Math.abs(ry) < 0.5) st.vprev = true;
    if (rx > 0.7 && Math.abs(ry) < 0.5) st.vnext = true;
  }
  return st;
}
// Never let an error in one action kill controller input for good.
function safeInput(a, repeat = false) {
  try { input(a, repeat); } catch (e) { console.error(e); reportError(e, "input " + a); toast("Error: " + e.message, 6000); }
}
// Errors in the page go to the app's log (logs/app.log), at most 20 a minute.
let reported = [];
function reportError(err, where = "") {
  const now = Date.now();
  reported = reported.filter(t => now - t < 60000);
  if (reported.length >= 20) return;
  reported.push(now);
  const message = `${where ? where + ": " : ""}${err?.message || err}`;
  fetch("/api/log", { method: "POST", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ level: "error", message, stack: err?.stack || "" }) }).catch(() => {});
}
window.addEventListener("error", e => reportError(e.error || e.message, "page"));
window.addEventListener("unhandledrejection", e => reportError(e.reason, "promise"));
// ---------------------------------------------------------------- pause while Heroic/a game is in front
// The Gamepad API keeps reporting buttons even when another window (Heroic, a game)
// is on top, so we ignore the pad when unfocused, and after launching/installing we
// pause until the user is back here.
let paused = null; // { sawBlur, comboStart }
function pauseFor(title, sub) {
  pauseInline(true);
  paused = { sawBlur: !document.hasFocus(), comboStart: 0 };
  $("pausedTitle").textContent = title;
  $("pausedSub").textContent = sub;
  $("paused").hidden = false;
}
function resume(quiet = false) {
  if (!paused) return;
  paused = null;
  $("paused").hidden = true;
  if (document.hasFocus()) pauseInline(false);
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

function padLoop(now) {
  requestAnimationFrame(padLoop);  // schedule first, so an exception can't stop the loop
  notePadButtons();
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
      } else if (JUMP[k] && !h.jumped && now - h.start >= JUMP_HOLD_MS) {
        h.jumped = true;
        safeInput(JUMP[k]);
      } else if (REPEAT.has(k) && now >= h.next) {
        const fast = now - h.start > 1500;
        h.next = now + (fast ? 35 : 70);
        safeInput(k, true);
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
  Escape: "start", Backspace: "b", x: "x", s: "x", y: "y", g: "b", q: "lb", "[": "lb", e: "rb", "]": "rb",
  PageUp: "lt", PageDown: "rt", r: "select", m: "start", Tab: "select", Home: "home", End: "end", v: "r3",
};
document.addEventListener("keydown", e => {
  if (paused) { e.preventDefault(); resume(); return; }  // a key event means this window has focus
  if (document.activeElement === els.search) {
    if (["Escape", "Enter", "ArrowDown", "Tab"].includes(e.key)) { e.preventDefault(); els.search.blur(); }
    return;
  }
  if (S.modal?.kind === "keyboard") {   // a physical keyboard types into the on-screen one too
    const m = S.modal;
    if (e.key.length === 1 && !e.ctrlKey && !e.altKey) { e.preventDefault(); m.value += e.key; m.onChange?.(m.value); renderModal(); return; }
    if (e.key === "Backspace") { e.preventDefault(); kbPress(m, "⌫ Delete"); return; }
    if (e.key === "Enter") { e.preventDefault(); kbPress(m, "✓ Done"); return; }
    if (e.key === "Escape") { e.preventDefault(); kbInput(m, "b"); return; }
  }
  if (document.activeElement?.id === "promptInput") {
    if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); modalInput(e.key === "Enter" ? "a" : "b"); }
    return;
  }
  if (e.key === "/") { e.preventDefault(); focusSearch(); return; }
  const a = KEYS[e.key];
  if (a) { e.preventDefault(); safeInput(a, e.repeat); }
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
