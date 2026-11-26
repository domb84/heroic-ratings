"use strict";
// Heroic Ratings: Controller and keyboard input; pausing while Heroic or a game is in front.
// (Plain scripts sharing one global scope, loaded in order by index.html.)
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
  api("/api/log", { level: "error", message, stack: err?.stack || "" }).catch(() => {});
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

