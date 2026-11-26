"use strict";
// Heroic Ratings: Building and drawing the game list; the value shown for each sort.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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

