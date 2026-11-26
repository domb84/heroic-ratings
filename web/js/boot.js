"use strict";
// Heroic Ratings: The Desktop-controls warning, and start-up.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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
