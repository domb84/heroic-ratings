"use strict";
// Heroic Ratings: The app's own on-screen keyboard.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
// ---------------------------------------------------------------- on-screen keyboard
// D-pad moves, A types, X deletes, Y space, LB capitals, RB pastes, View shows/hides a
// secret, Menu done, B cancel.
const KB_ROWS = [
  [..."1234567890"], [..."qwertyuiop"], [..."asdfghjkl'"], [..."zxcvbnm,.-"],
];
// the bottom row depends on the field: Show/Hide only for secrets
function kbRows(m) {
  return [...KB_ROWS, ["⇧ Caps", "Space", "⌫ Delete", "Paste", ...(m.secret ? [m.reveal ? "Hide" : "Show"] : []), "✓ Done"]];
}
function openKeyboard(opts) {
  openModal({ kind: "keyboard", r: 1, c: 0, caps: false, reveal: false, value: opts.value || "", ...opts });
}
function kbKey(m, r = m.r, c = m.c) { const rows = kbRows(m); return rows[r][Math.min(c, rows[r].length - 1)]; }
function kbPress(m, key) {
  if (key === "⇧ Caps") m.caps = !m.caps;
  else if (key === "Space") m.value += " ";
  else if (key === "⌫ Delete") m.value = m.value.slice(0, -1);
  else if (key === "Paste") { pasteInto(m); return; }
  else if (key === "Show" || key === "Hide") m.reveal = !m.reveal;
  else if (key === "✓ Done") { closeModal(); m.onDone?.(m.value); return; }
  else m.value += m.caps ? key.toUpperCase() : key;
  m.onChange?.(m.value);
  if (S.modal === m) renderModal();
}
function kbInput(m, a) {
  const rows = kbRows(m).length, wide = r => kbRows(m)[r].length;
  // moving between the 10-key rows and the shorter bottom row keeps the position roughly
  const toRow = (r, nr) => { m.c = Math.round(m.c * (wide(nr) - 1) / Math.max(1, wide(r) - 1)); m.r = nr; };
  if (["up", "down", "left", "right"].includes(a)) m.typed = false;   // back to the on-screen keys
  switch (a) {
    case "up": if (m.r > 0) toRow(m.r, m.r - 1); break;
    case "down": if (m.r < rows - 1) toRow(m.r, m.r + 1); break;
    case "left": m.c = (m.c - 1 + wide(m.r)) % wide(m.r); break;
    case "right": m.c = (m.c + 1) % wide(m.r); break;
    case "a": return kbPress(m, kbKey(m));
    case "x": return kbPress(m, "⌫ Delete");
    case "y": return kbPress(m, "Space");
    case "lb": return kbPress(m, "⇧ Caps");
    case "rb": return kbPress(m, "Paste");
    case "select": if (m.secret) return kbPress(m, "Show"); break;
    case "start": return kbPress(m, "✓ Done");
    case "b": closeModal(); m.onCancel?.(); return;
  }
  renderModal();
}
function kbHTML(m) {
  const shown = m.secret && !m.reveal ? "•".repeat(m.value.length) : esc(m.value);
  return `<h3>${esc(m.title)}</h3>${m.html ? `<div class="kbhelp">${m.html}</div>` : ""}
    <div class="kbfield ${m.secret && m.reveal ? "revealed" : ""}">${shown}<span class="caret"></span></div>
    <div class="kb">${kbRows(m).map((row, r) => `<div class="kbrow">${row.map((k, c) =>
      `<span class="kkey ${r === m.r && c === Math.min(m.c, row.length - 1) ? "sel" : ""} ${k === "⇧ Caps" && m.caps ? "on" : ""} ${row.length < 10 ? "wide" : ""}"
             data-r="${r}" data-c="${c}">${esc(m.caps && k.length === 1 ? k.toUpperCase() : k)}</span>`).join("")}</div>`).join("")}</div>
    <div class="hint"><span><span class="btn a">A</span>Type</span><span><span class="btn x">X</span>Delete</span>
      <span><span class="btn y">Y</span>Space</span><span><span class="btn sh">LB</span>Caps</span><span><span class="btn sh">RB</span>Paste</span>
      ${m.secret ? `<span><span class="btn glyph"><svg viewBox="0 0 16 16"><path d="M2.5 5.5h7v7h-7zM6.5 3.5h7v7"/></svg></span>${m.reveal ? "Hide" : "Show"}</span>` : ""}
      <span><span class="btn glyph"><svg viewBox="0 0 16 16"><path d="M3 4.5h10M3 8h10M3 11.5h10"/></svg></span>Done</span><span><span class="btn b">B</span>Cancel</span></div>`;
}

// ---------------------------------------------------------------- pasting
// Keys and secrets are long and random: copy them (e.g. in Desktop Mode, or from Steam's
// own keyboard) and paste. In the app window the clipboard comes through its small bridge;
// in a plain browser, through the browser's clipboard API.
async function readClipboard() {
  try {
    if (window.shell?.readClipboard) return (await window.shell.readClipboard()) || "";
    if (navigator.clipboard?.readText) return (await navigator.clipboard.readText()) || "";
  } catch (e) { /* no permission / nothing there */ }
  return "";
}
// Pasted text, tidied: keys and IDs lose all spaces and line breaks (a copied key often
// brings a newline), other text is put on one line.
function cleanPaste(text, compact) {
  text = String(text || "").slice(0, 1000);
  return compact ? text.replace(/\s+/g, "") : text.replace(/\s+/g, " ").trim();
}
async function pasteInto(m, text) {
  if (text == null) text = await readClipboard();
  text = cleanPaste(text, m.compact);
  if (!text) { toast("Nothing to paste: the clipboard is empty", 2500); return; }
  if (m.kind === "prompt") {
    const inp = document.getElementById("promptInput");
    if (inp) {   // like a normal paste: replaces the selection (the old value is selected when the box opens)
      inp.setRangeText(text, inp.selectionStart ?? inp.value.length, inp.selectionEnd ?? inp.value.length, "end");
      m.value = inp.value;
      inp.focus();
    }
  } else {
    m.value += text;
    m.onChange?.(m.value);
  }
  toast(`Pasted ${m.secret ? text.length + " characters" : "“" + text.slice(0, 40) + (text.length > 40 ? "…" : "") + "”"}`, 1800);
  if (S.modal === m && m.kind === "keyboard") renderModal();
}
// Ctrl+V (a physical keyboard, or Steam's keyboard) while typing: same as Paste
document.addEventListener("paste", e => {
  const m = S.modal;
  if (!m || (m.kind !== "keyboard" && m.kind !== "prompt")) return;
  e.preventDefault();
  pasteInto(m, e.clipboardData?.getData("text") || "");
});
// The text box's Paste / Show buttons (mouse or touch)
els.modal.addEventListener("click", e => {
  const b = e.target.closest("[data-act]");
  if (!b || S.modal?.kind !== "prompt") return;
  promptAction(S.modal, b.dataset.act);
});
function promptAction(m, act) {
  const inp = document.getElementById("promptInput");
  if (inp) m.value = inp.value;
  if (act === "paste") pasteInto(m);
  else if (act === "reveal" && m.secret) { m.reveal = !m.reveal; renderModal(); }
}
els.modal.addEventListener("click", e => {   // touch / mouse
  const k = e.target.closest(".kkey");
  if (k && S.modal?.kind === "keyboard") { S.modal.r = +k.dataset.r; S.modal.c = +k.dataset.c; kbPress(S.modal, kbKey(S.modal)); }
});

