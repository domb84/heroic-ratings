"use strict";
// Heroic Ratings: The app's own on-screen keyboard.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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

