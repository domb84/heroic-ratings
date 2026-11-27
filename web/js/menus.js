"use strict";
// Heroic Ratings: Dialogs, the genre picker and the main menu.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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
      <input id="promptInput" type="${m.secret && !m.reveal ? "password" : "text"}" autocomplete="off" spellcheck="false" value="${esc(m.value || "")}">
      <div class="prompt-acts"><button type="button" data-act="paste">Paste</button>${m.secret ? `<button type="button" data-act="reveal">${m.reveal ? "Hide" : "Show"}</button>` : ""}</div>
      <div class="hint"><span>Enter OK</span><span>Esc Cancel</span><span>Ctrl+V or RB paste</span>${m.secret ? `<span>View ${m.reveal ? "hide" : "show"}</span>` : ""}</div>`;
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
  if (items[m.sel]?.heading) m.sel = selectable(items, m.sel, 1);
  els.modal.innerHTML = `<h3>${esc(m.title)}<small>${m.hint || ""}</small></h3>
    <div class="opts">${items.map((it, i) => it.heading ? `<div class="opt-h">${esc(it.label)}</div>` : `<div class="opt ${i === m.sel ? "sel" : ""} ${it.on ? "on" : ""} ${it.info ? "info" : ""} ${it.loading ? "loading" : ""}" data-i="${i}">
      <span>${esc(it.label)}</span>${it.value != null ? `<span class="v ${it.change ? "arrows" : ""}">${esc(it.value)}</span>` : ""}</div>`).join("")}</div>`;
  els.modal.querySelector(".opt.sel")?.scrollIntoView({ block: "nearest" });
}

// Group headings in a list can't be selected: the cursor moves past them.
function selectable(items, i, dir) {
  for (let j = i; j >= 0 && j < items.length; j += dir) if (!items[j].heading) return j;
  for (let j = i; j >= 0 && j < items.length; j -= dir) if (!items[j].heading) return j;
  return i;
}
function modalInput(a) {
  const m = S.modal;
  if (m.kind === "keyboard") return kbInput(m, a);
  if (m.kind === "prompt") {
    if (a === "a") { const v = document.getElementById("promptInput").value; closeModal(); m.onSave(v); }
    else if (a === "b" || a === "start") { closeModal(); m.onCancel?.(); }
    else if (a === "rb") promptAction(m, "paste");
    else if (a === "select") promptAction(m, "reveal");
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
    case "up": m.sel = selectable(items, Math.max(0, m.sel - cols), -1); break;
    case "down": m.sel = selectable(items, Math.min(items.length - 1, m.sel + cols), 1); break;
    case "left": if (cols > 1) m.sel = Math.max(0, m.sel - 1); else it?.change?.(-1); break;
    case "right": if (cols > 1) m.sel = Math.min(items.length - 1, m.sel + 1); else it?.change?.(1); break;
    case "lt": m.sel = selectable(items, Math.max(0, m.sel - cols * 6), -1); break;
    case "home": m.sel = selectable(items, 0, 1); break;                    // hold LT
    case "end": m.sel = selectable(items, items.length - 1, -1); break;     // hold RT
    case "rt": m.sel = selectable(items, Math.min(items.length - 1, m.sel + cols * 6), 1); break;
    case "a": if (it) { if (it.activate) it.activate(); else it.change?.(1); } break;
    case "b": case "start": case "y": closeModal(); m.onClose?.(); return;
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
    title: "Menu", kind: "list menu",
    hint: `<span>◀ ▶ change</span><span><span class="btn a">A</span>Select</span><span><span class="btn b">B</span>Close</span>`,
    items: () => {
      const game = g ? [
        ...(!g.extra && !g.steam_appid ? [{ label: `Steam match for “${g.title}”…`, value: g.appid ? (g.override ? "set by you" : "app " + g.appid) : "none",
                          activate: () => matchFlow(g, g.title) }] : []),
        ...(g.installed && g.managed !== false && !S.downloads[g.id] ? [{ label: `Uninstall “${g.title}”`, activate: () => { closeModal(); uninstallFlow(g); } }] : []),
        ...(S.downloads[g.id]?.queued ? [{ label: `Cancel queued ${S.downloads[g.id].kind} of “${g.title}”`, activate: () => cancelQueued(g) }] : []),
      ] : [];
      return [
        ...(game.length ? [{ heading: true, label: "Selected game" }, ...game] : []),
        { heading: true, label: "Filters" },
        { label: "Store", value: S.store, change: d => { S.store = cycle(storesPresent, S.store, d); refilter(); } },
        { label: "Installed only", value: S.installedOnly ? "On" : "Off", change: () => { S.installedOnly = !S.installedOnly; refilter(); } },
        { label: "Hide unrated games", value: S.hideUnrated ? "On" : "Off", change: () => { S.hideUnrated = !S.hideUnrated; refilter(); } },
        ...(document.body.classList.contains("lengths") ? [
          { label: "Play style", value: S.style, change: d => { S.style = cycle(Object.keys(STYLES), S.style, d); refilter(); } },
          { label: "Perspective", value: S.persp, change: d => { S.persp = cycle(Object.keys(PERSPECTIVES), S.persp, d); refilter(); } },
        ] : []),
        { heading: true, label: "Ratings" },
        ...(unmatched ? [{ label: `Match unmatched games (${unmatched})`, activate: rematchUnmatched }] : []),
        { label: "Refresh", activate: () => { api("/api/refresh", {}); closeModal(); toast("Refreshing…"); } },
        { heading: true, label: "App" },
        ...(S.update ? [{ label: S.update.can_install ? `Update to ${S.update.version}` : `Update ${S.update.version} is out…`,
                          value: S.update.can_install ? updateValue(S.update) : undefined,
                          activate: () => S.update.can_install ? updateFlow(S.update) : openSettings() }] : []),
        { label: "Settings…", activate: () => openSettings() },
        { label: "About…", activate: openAbout },
        { label: "Quit", activate: quit },
      ];
    },
  });
}

