"use strict";
// Heroic Ratings: Settings, app state and small helpers.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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

