"use strict";
// Heroic Ratings: The details pane: trailers (Steam / YouTube) and everything about the selected game.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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

