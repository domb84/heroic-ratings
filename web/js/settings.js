"use strict";
// Heroic Ratings: The settings page, typing text (on-screen keyboard or text box), picking a Steam match.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
// ---------------------------------------------------------------- settings
// Twitch/IGDB credentials. The secret never comes back from the backend; values typed
// here are only sent when saved, after Twitch has accepted them.
const settingsDraft = { client_id: "", client_secret: "", steam_key: "" };
async function openSettings(sel = 0) {
  let st, upd, sk;
  try { ({ twitch: st, updates: upd, steam_key: sk } = await api("/api/settings", {})); } catch { toast("Couldn't load settings", 3000); return; }
  const fromEnv = st.source === "env";
  const draftSet = settingsDraft.client_id || settingsDraft.client_secret;
  const u = upd.available;
  const items = () => [
    ...(u || upd.checkable ? [{ heading: true, label: "Updates" }] : []),
    ...(u ? [u.can_install ? { label: `Update to ${u.version}`, value: updateValue(u), activate: () => updateFlow(u) }
                           : { info: true, label: "Update available", value: u.version }] : []),
    ...(upd.checkable ? [{ label: "Check for updates", value: upd.enabled ? "On" : "Off", activate: () => toggleUpdates(!upd.enabled) }] : []),
    { heading: true, label: "Steam" },
    ...(sk.source === "env" ? [{ info: true, label: "Steam Web API key", value: "Set by environment" }] : [
      { label: "Steam Web API key", value: settingsDraft.steam_key ? "•".repeat(10) + " (new)" : sk.set ? sk.hint : "Not set",
        activate: () => editSetting("steam_key", "Steam Web API key", true) },
      ...(settingsDraft.steam_key ? [{ label: "Save and check with Steam", activate: saveSteamKey }] : []),
    ]),
    ...(sk.set && !settingsDraft.steam_key ? [{ label: "Test Steam key", activate: testSteamKey }] : []),
    ...(sk.set && sk.source !== "env" && !settingsDraft.steam_key ? [{ label: "Remove Steam key", activate: removeSteamKey }] : []),
    ...(sk.set ? [] : [{ info: true, label: "Optional: matches your library to Steam in seconds instead of minutes. Free key from steamcommunity.com/dev/apikey." }]),
    { heading: true, label: "IGDB" },
    ...(fromEnv ? [{ info: true, label: "Twitch key (for IGDB)", value: "Set by environment" }] : []),
    ...(fromEnv ? [] : [
      { label: "Twitch client ID", value: settingsDraft.client_id ? settingsDraft.client_id : st.set ? st.client_id_hint : "Not set",
        activate: () => editSetting("client_id", "Twitch client ID", false) },
      { label: "Twitch client secret", value: settingsDraft.client_secret ? "•".repeat(10) + " (new)" : st.set ? "•".repeat(10) : "Not set",
        activate: () => editSetting("client_secret", "Twitch client secret", true) },
      ...(draftSet ? [{ label: "Save and check with Twitch", activate: saveTwitch }] : []),
      ...(st.set ? [{ label: "Remove credentials", activate: removeTwitch }] : []),
    ]),
    ...(st.set && !draftSet ? [{ label: "Test IGDB connection", activate: testTwitch }] : []),
    ...(st.set || fromEnv ? [] : [{ info: true, label: "Optional: adds critic scores, play times and videos. Free key from dev.twitch.tv/console." }]),
  ];
  if (!sel) sel = items().findIndex(i => !i.info && !i.heading);   // start on the first thing you can change
  openModal({ kind: "list settings", title: "Settings", hint: "A select · B close", items, sel });
}
// Steam Web API key: checked with Steam before it's saved; never shown again in full.
async function saveSteamKey() {
  toast("Checking with Steam…", 2000);
  let r;
  try { r = await api("/api/settings/steam-key", { key: settingsDraft.steam_key }); } catch { r = { ok: false, error: "No reply from the app" }; }
  if (r.ok) {
    settingsDraft.steam_key = "";
    closeModal();
    toast("Steam accepted the key. Matching the rest of your library…", 4000);
  } else {
    openModal({ kind: "confirm", yes: "OK", html: esc(r.error), onYes: () => openSettings() });
  }
}
async function testSteamKey() {
  toast("Testing with Steam…", 3000);
  let r;
  try { r = await api("/api/settings/steam-key-test", {}); } catch { r = { ok: false, error: "No reply from the app" }; }
  if (r.ok) toast("The Steam key is working", 2500);
  else openModal({ kind: "confirm", yes: "OK", html: `Steam key test failed: ${esc(r.error)}`, onYes: () => openSettings() });
}
async function removeSteamKey() {
  const r = await api("/api/settings/steam-key", { remove: true });
  if (r.ok) { toast("Steam key removed", 2500); openSettings(); }
  else openModal({ kind: "confirm", yes: "OK", html: esc(r.error), onYes: () => openSettings() });
}
function updateValue(u) {
  const ins = u.install || {};
  return ins.phase === "downloading" ? "Downloading…" : ins.phase === "done" ? "Installed: restart the app" : "Download and install";
}
// One-button update: the backend downloads the release's AppImage, checks it against the
// release's .sha256, and swaps it in; the app then asks to be restarted.
function updateFlow(u) {
  const ins = u.install || {};
  if (ins.phase === "downloading") { toast("Already downloading the update", 2500); return; }
  if (ins.phase === "done") { openModal({ kind: "confirm", yes: "Quit now", onYes: quit,
    html: `${esc(u.version)} is installed.<div class="note">Quit, then start Heroic Ratings again to use it.</div>` }); return; }
  const mb = u.size ? ` (${Math.round(u.size / 1048576)} MB)` : "";
  openModal({ kind: "confirm", yes: "Update", onYes: async () => {
    let r;
    try { r = await api("/api/update-install", {}); } catch { r = { ok: false, error: "no reply from the app" }; }
    if (r.ok) toast(`Downloading ${u.version}… you can keep using the app`, 4000);
    else toast(`Can't update from here (${r.error})`, 5000);
  }, html: `Download <b>Heroic Ratings ${esc(u.version)}</b>${mb} and replace this one?
    <div class="note">It's checked against the release's checksum before anything is replaced. Your library, ratings, queue and settings are kept. You restart the app when it's done.</div>` });
}
async function toggleUpdates(on) {
  const back = S.modal.sel;
  try { await api("/api/settings/updates", { enabled: on }); } catch { toast("Couldn't change it", 3000); return; }
  if (!on) showUpdate(null);
  openSettings(back);
}
function editSetting(key, title, secret) {
  const back = S.modal.sel;
  askText({ title, secret, compact: true, value: settingsDraft[key],
    html: key === "steam_key" ? "At steamcommunity.com/dev/apikey: sign in, enter any domain name (e.g. localhost), agree, and copy the key. It's stored only on this device."
      : secret ? "Paste or type the secret. It's stored only on this device."
      : "At dev.twitch.tv/console: Register Your Application (OAuth redirect URL http://localhost, client type Confidential), then copy the Client ID and make a New Secret.",
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
async function testTwitch() {
  toast("Testing with Twitch and IGDB…", 3000);
  let r;
  try { r = await api("/api/settings/twitch-test", {}); } catch { r = { ok: false, error: "No reply from the app" }; }
  if (r.ok) toast("IGDB is working", 2500);
  else openModal({ kind: "confirm", yes: "OK", html: `IGDB test failed: ${esc(r.error)}`, onYes: () => openSettings() });
}
// About: this app's version, the Heroic it drives, and where the data comes from.
async function openAbout() {
  let version, heroic;
  try { ({ version, heroic } = await api("/api/settings", {})); } catch { toast("Couldn't load the versions", 3000); return; }
  const hv = heroic.version ? heroic.version + (heroic.status === "ok" ? "" : ` · ${heroicStatusText(heroic)}`) : "Not found";
  openModal({ kind: "list settings", title: "About", hint: "B back", sel: 0, onClose: openMenu,
    items: () => [
      { info: true, label: "Heroic Ratings", value: version || "dev" },
      { info: true, label: "Heroic", value: hv },
      { info: true, label: "Ratings and details from Steam, Wikidata and IGDB.com" },
    ] });
}
async function removeTwitch() {
  const r = await api("/api/settings/twitch", { remove: true });
  if (r.ok) { toast("Credentials removed", 2500); openSettings(); }
  else openModal({ kind: "confirm", yes: "OK", html: esc(r.error), onYes: () => openSettings() });
}

// Typing text: the on-screen keyboard on a Steam Deck, a text box on a PC.
function askText({ title, value = "", secret = false, compact = false, html = "", onDone, onCancel }) {
  if (S.steamDeck) openKeyboard({ title, value, secret, compact, html, onDone, onCancel });
  else openModal({ kind: "prompt", title, secret, compact, value, html, onSave: onDone, onCancel });
}

// Menu → Steam match: pick the right Steam page for a game (or none). Results come from
// Steam's store search and IGDB (games listing a Steam page); a number is a Steam app ID.
function matchFlow(g, query) {
  askText({ title: `Steam match for ${g.title}`, value: query,
    html: "Search by name, or type the Steam app ID (the number in the game's Steam store address).",
    onDone: q => q.trim() && matchResults(g, q.trim()) });
}
async function matchResults(g, q) {
  // the results window opens straight away, saying it's searching (B cancels)
  const m = { kind: "list", title: `Steam match for ${g.title}`, hint: "B cancel",
              items: () => [{ info: true, loading: true, label: `Searching Steam for “${q}”…` }] };
  openModal(m);
  let r;
  try { r = await api("/api/match-search", { id: g.id, query: q }); } catch { r = { results: [] }; }
  if (S.modal !== m) return;          // cancelled while searching
  const res = r.results || [];
  const items = () => [
    ...(res.length ? [] : [{ info: true, label: `Nothing found for “${q}”. Try another name, or type the Steam app ID.` }]),
    ...res.map(x => ({ label: x.name, value: [x.year, x.src, "app " + x.appid].filter(Boolean).join(" · "),
                       on: x.appid === g.appid, activate: () => setMatch(g, x.appid, x.name) })),
    { label: "Search again…", activate: () => matchFlow(g, q) },
    ...(g.appid ? [{ label: "Not on Steam (remove the match)", activate: () => setMatch(g, null) }] : []),
  ];
  Object.assign(m, { hint: "A pick · B cancel", items, sel: res.length ? 0 : items().length - 1 });
  renderModal();
}
async function setMatch(g, appid, name) {
  closeModal();
  await api("/api/override", { id: g.id, appid });
  toast(appid ? `Matched to ${name}. Fetching its reviews…` : "Match removed", 3500);
  setTimeout(loadGames, 600);
}

