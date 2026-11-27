"""Driving Heroic: starting it hidden with its debug port, running scripts in its window,
installs, uninstalls, progress, and launching games through their Steam shortcuts.

Everything that depends on Heroic's version (library files, launch URLs, the window API)
lives in an adapter (base.py, v2.py), picked from the installed version (version.py).
This module is the version-independent plumbing, and what the rest of the app calls.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .. import steam as steam_api
from .. import util
from ..config import HEROIC_FLATPAK_ID, HOME
from ..logs import log
from . import version as heroic_version
from .base import HeroicAdapter


# ------------------------------------------------------------------- which Heroic

_compat: dict | None = None
_api_missing: dict[int, list[str]] = {}     # debug port -> window API functions missing


def compat(refresh: bool = False) -> dict:
    """{"adapter", "version", "status", "missing"} for the installed Heroic (worked out once)."""
    global _compat
    if _compat is None or refresh:
        v = heroic_version.detect_version()
        a, status = heroic_version.pick(v)
        _compat = {"adapter": a, "version": v, "status": status, "missing": []}
        _api_missing.clear()
        log(f"Heroic {v or '(version unknown)'}: using the {a.name} adapter (tested with {a.tested}), status {status}",
            level=logging.WARNING if status == "unsupported" else logging.INFO)
    return _compat


def adapter() -> HeroicAdapter:
    return compat()["adapter"]


def installs_allowed() -> bool:
    c = compat()
    return (c["status"] != "unsupported" or bool(os.environ.get("HEROIC_RATINGS_ALLOW_UNSUPPORTED"))) \
        and not c["missing"]


def compatibility() -> dict:
    """For the UI (Settings and the start-up warning)."""
    c = compat()
    a = c["adapter"]
    return {"version": c["version"], "adapter": a.name, "tested": a.tested,
            "status": "api-changed" if c["missing"] else c["status"], "missing": c["missing"],
            "installs": installs_allowed()}


# ------------------------------------------------------------------- launching

def heroic_command(runner: str, app_name: str, show_gui: bool = False) -> list[str]:
    """Command that opens Heroic's launch URL for a game.

    For an installed game Heroic launches it straight away (window kept hidden).
    For a game that isn't installed, Heroic itself asks "do you wish to install it?"
    """
    url = adapter().launch_url(runner, app_name, show_gui)
    tmpl = os.environ.get("HEROIC_LAUNCH_CMD")
    if tmpl:
        return [p.replace("{url}", url) for p in tmpl.split()]
    flags = ["--no-sandbox"] if show_gui else ["--no-gui", "--no-sandbox"]
    if shutil.which("flatpak") and (HOME / ".var/app" / HEROIC_FLATPAK_ID).is_dir():
        return ["flatpak", "run", HEROIC_FLATPAK_ID, *flags, url]
    if shutil.which("heroic"):
        return ["heroic", *flags, url]
    return ["xdg-open", url]


# --no-gui: Heroic's window stays hidden (it still loads, so the debug port works).
HEROIC_DEBUG_ARGS = ["--no-gui", "--no-sandbox", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1"]


def heroic_debug_command() -> list[str]:
    """Start Heroic hidden, with its debug port (it runs as part of this app)."""
    if shutil.which("flatpak") and (HOME / ".var/app" / HEROIC_FLATPAK_ID).is_dir():
        return ["flatpak", "run", HEROIC_FLATPAK_ID, *HEROIC_DEBUG_ARGS]
    if shutil.which("heroic"):
        return ["heroic", *HEROIC_DEBUG_ARGS]
    raise OSError("Heroic isn't installed (neither the Flatpak nor a 'heroic' command was found)")


# ------------------------------------------------------------------- Heroic's window
#
# Heroic 2.22.4 can't start an install from outside: its heroic://launch prompt
# says "install?", but the Yes button is broken (Heroic issue #6053). Instead we
# start Heroic with Chromium's debug port (bound to 127.0.0.1) and call the same
# functions its own install dialog uses, from inside its window. Heroic does all the work.


class _MiniWebSocket:
    """Just enough of a WebSocket client (RFC 6455) to talk to Chromium's debug port."""

    def __init__(self, url: str, timeout: float = 10):
        u = urllib.parse.urlparse(url)
        if u.hostname not in ("127.0.0.1", "localhost"):
            raise OSError("refusing non-local debug socket")
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = __import__("base64").b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("debug socket closed during handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("debug socket refused: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def send(self, text: str):
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                               bytes([0x80 | 126]) + n.to_bytes(2, "big") if n < 65536 else
                               bytes([0x80 | 127]) + n.to_bytes(8, "big"))
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("debug socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 0x8:
                raise OSError("debug socket closed by Heroic")
            if op in (0x9, 0xA):      # ping/pong: ignore
                continue
            parts.append(payload)
            if b0 & 0x80:
                return b"".join(parts).decode()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _heroic_page_ws(port: int) -> str | None:
    """WebSocket URL of Heroic's main window on its debug port, or None if not reachable."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as r:
            targets = json.load(r)
    except (OSError, ValueError):
        return None
    if not isinstance(targets, list):
        return None
    targets = [t for t in targets if isinstance(t, dict)]
    pages = [t for t in targets if t.get("type") == "page" and "index.html" in t.get("url", "")]
    pages = pages or [t for t in targets if t.get("type") == "page"]
    return pages[0].get("webSocketDebuggerUrl") if pages else None


def _cdp_eval(ws_url: str, expr: str, timeout: float = 20):
    ws = _MiniWebSocket(ws_url, timeout=timeout)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                break
    finally:
        ws.close()
    if "error" in msg:   # e.g. "Execution context was destroyed." while the page navigates
        raise RuntimeError(msg["error"].get("message", "debug protocol error"))
    res = msg.get("result", {})
    if "exceptionDetails" in res:
        ex = res["exceptionDetails"]
        raise RuntimeError((ex.get("exception") or {}).get("description") or ex.get("text", "script error"))
    return res.get("result", {}).get("value")


def _heroic_processes() -> list[tuple[int, bytes]]:
    """(pid, command line) of Heroic's main process(es), Flatpak or native."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if (HEROIC_FLATPAK_ID.encode() in cmd and b"flatpak run" not in cmd) or b"/heroic/heroic" in cmd \
                or cmd.startswith(b"heroic "):
            if b"--type=" not in cmd:   # skip Chromium helper processes
                out.append((int(d.name), cmd))
    return out


def heroic_running() -> bool:
    return bool(_heroic_processes())


def stop_our_heroic(heroic_dir: Path | None, wait: float = 10) -> dict:
    """Close the hidden Heroic this app started, so that a game's Steam shortcut starts a
    fresh Heroic belonging to Steam. (Otherwise the shortcut hands the launch to our
    Heroic, and the game runs inside this app.) Refuses while it's downloading or
    uninstalling something. Returns {} or {"error": "heroic-busy", "busy": [...]}."""
    d = heroic_downloads(heroic_dir)
    if d.get("running"):
        busy = [q["runner"] + ":" + q["appName"] for q in d.get("queue", [])] + \
               [u["runner"] + ":" + u["appName"] for u in d.get("uninstalling", [])]
        if busy:
            return {"error": "heroic-busy", "busy": busy}
    ours = [pid for pid, cmd in _heroic_processes() if b"--remote-debugging-port" in cmd]
    if not ours:
        return {}
    log("closing our hidden Heroic before launching through Steam:", ours)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in ours:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.time() + wait
        while time.time() < deadline:
            if not any(b"--remote-debugging-port" in cmd for _, cmd in _heroic_processes()):
                time.sleep(1)    # let the Flatpak sandbox and Heroic's lock go away too
                return {}
            time.sleep(0.25)
    return {"error": "heroic-not-stopped"}


def _active_debug_port(heroic_dir: Path | None, newer_than: float = 0) -> int | None:
    """Port Heroic's Chromium chose (we pass port 0), from DevToolsActivePort in its
    profile folder, which is Heroic's config folder."""
    if not heroic_dir:
        return None
    f = heroic_dir / "DevToolsActivePort"
    try:
        if f.stat().st_mtime < newer_than:
            return None
        return int(f.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _ready_page(port: int, deadline: float) -> str | None:
    """The debug socket of Heroic's window once its UI has loaded, or None by the deadline.
    (Heroic's debug port and window exist before its page has loaded: it starts on a blank
    page and then navigates to its UI, which destroys a script run in between.)"""
    while True:
        ws = _heroic_page_ws(port)
        if ws:
            try:
                if _cdp_eval(ws, adapter().ready_js, timeout=5) is True:
                    return ws
            except (OSError, RuntimeError, ValueError):
                pass   # navigating / not ready yet
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def ensure_debug_heroic(heroic_dir: Path | None, wait: float = 45) -> dict:
    """Make sure a hidden Heroic with its debug port is running (started by us if needed)
    and its UI has loaded.

    Returns {"ws": <debug socket of Heroic's window>} or {"error": ...}. A Heroic started
    some other way has no debug port, so the user is asked to quit it instead.
    """
    deadline = time.time() + wait
    port = _active_debug_port(heroic_dir)
    if port and _heroic_page_ws(port):
        ws = _ready_page(port, deadline)
        return {"ws": ws} if ws else {"error": "heroic-not-ready"}
    if heroic_running():
        return {"error": "heroic-running"}
    started = time.time()
    util.spawn_detached(heroic_debug_command())
    while time.time() < deadline:
        time.sleep(0.5)
        port = _active_debug_port(heroic_dir, newer_than=started - 2)
        if port and _heroic_page_ws(port):
            ws = _ready_page(port, deadline)
            if ws:
                log(f"Heroic ready after {time.time() - started:.1f}s")
                return {"ws": ws}
            break
    log("Heroic didn't come up (debug port file:", port, ")", level=logging.WARNING)
    return {"error": "heroic-not-started"}


def heroic_eval(heroic_dir: Path | None, js: str, timeout: float, what: str) -> dict:
    """Run a script in Heroic's window: {"value": ...} or {"error": ..., "detail": ...}.
    A script interrupted by Heroic still loading is retried (twice)."""
    last = None
    for attempt in range(3):
        r = ensure_debug_heroic(heroic_dir)
        if "error" in r:
            log(what, "failed:", r["error"], level=logging.WARNING)
            return r
        try:
            return {"value": _cdp_eval(r["ws"], js, timeout=timeout)}
        except (OSError, RuntimeError, ValueError) as e:
            last = e
            log(what, f"failed (attempt {attempt + 1} of 3):", repr(e), level=logging.WARNING)
            time.sleep(1.5)
    return {"error": "script", "detail": str(last)}


def check_api(heroic_dir: Path | None) -> dict | None:
    """Before the first script in a Heroic: does its window still have every function the
    adapter calls? Catches a Heroic update that changed them, before anything half-works.
    Returns None when all is well, else the error to show."""
    c = compat()
    if not installs_allowed() and not c["missing"]:
        return {"error": "heroic-unsupported",
                "detail": f"Heroic {c['version']} isn't supported yet (this app works with {adapter().name})"}
    port = _active_debug_port(heroic_dir)
    if port not in _api_missing or not port:
        r = heroic_eval(heroic_dir, adapter().probe_js(), 15, "Heroic API check")
        if "error" in r:
            return r
        missing = [str(m) for m in r["value"]] if isinstance(r["value"], list) else []
        port = _active_debug_port(heroic_dir)       # Heroic may have just been started
        if port:
            _api_missing[port] = missing
        c["missing"] = missing
        if missing:
            log(f"Heroic {c['version']} lacks what the {adapter().name} adapter needs: {', '.join(missing)}",
                level=logging.WARNING)
    if _api_missing.get(port):
        return {"error": "heroic-api-changed", "detail": "Heroic no longer has: " + ", ".join(_api_missing[port])}
    return None


def _window(heroic_dir: Path | None, js: str, timeout: float, what: str, runner: str = "") -> dict:
    """heroic_eval, after checking this Heroic (and this store) is one the adapter can drive."""
    if runner and not adapter().manages(runner):
        store = adapter().store_labels.get(runner, runner)
        return {"error": "heroic-store-unmanaged", "detail": f"{store} games are installed and uninstalled in Heroic itself for now"}
    err = check_api(heroic_dir)
    return err or heroic_eval(heroic_dir, js, timeout, what)


def launch_game(runner: str, app_name: str, heroic_dir: Path | None) -> dict:
    """Launch a game through its own Steam shortcut, so it runs as its own Steam app and
    not inside ours. Refuses (with the reason) when Steam can't launch it yet. A game that
    is a Steam game (Heroic 3's Steam store) is launched through Steam directly."""
    appid = adapter().steam_appid(runner, app_name)
    if appid:
        stopped = stop_our_heroic(heroic_dir)
        if "error" in stopped:
            return stopped
        steam_api.steam_run(appid)
        return {"via": "steam-app"}
    status = steam_api.game_shortcut_status(runner, app_name)
    if status != "ready":
        return {"error": {"missing": "no-shortcut", "restart": "restart-steam"}.get(status, status)}
    stopped = stop_our_heroic(heroic_dir)
    if "error" in stopped:
        return stopped
    steam_api.steam_run(steam_api.shortcut_gameid(steam_api.find_game_shortcut(runner, app_name)[0]))
    return {"via": "steam-shortcut"}


# ------------------------------------------------------------------- installing / uninstalling

def install_locations(default_path: str | None) -> list[dict]:
    """Heroic's default folder plus any SD card / USB drive, each with free space."""
    def free(path: Path) -> int | None:
        p = path
        while not p.exists() and p != p.parent:
            p = p.parent
        try:
            return shutil.disk_usage(p).free
        except OSError:
            return None
    locs = []
    if default_path:
        locs.append({"label": "Heroic's default", "path": default_path, "free": free(Path(default_path))})
    # SteamOS mounts SD cards at /run/media/<label> (older: /run/media/<user>/<label>).
    mounts = []
    try:
        for d in sorted(Path("/run/media").iterdir()):
            if os.path.ismount(d):
                mounts.append(d)
            elif d.is_dir():
                mounts += sorted(x for x in d.iterdir() if os.path.ismount(x))
    except OSError:
        pass
    for mnt in mounts:
        path = str(mnt / "Games" / "Heroic")
        if path != default_path:
            locs.append({"label": f"{mnt.name} (removable)", "path": path, "free": free(mnt)})
    return locs


def install_options(runner: str, app_name: str, platform: str, heroic_dir: Path | None) -> dict:
    r = _window(heroic_dir, adapter().install_options_js(runner, app_name, platform or "Windows"), 100, "install options",
                runner)
    if "error" in r:
        return r
    opts = r["value"]
    if not isinstance(opts, dict):
        return {"error": "script", "detail": repr(opts)}
    if "error" not in opts:
        opts["locations"] = install_locations(opts.get("defaultPath"))
    return opts


def install_with_options(runner: str, app_name: str, o: dict, heroic_dir: Path | None) -> dict:
    """Queue an install in Heroic with the options chosen in our app."""
    def strs(v):
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    path = o.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return {"error": "bad-path"}
    platform = o.get("platform") if o.get("platform") in ("Windows", "linux") else "Windows"
    choice = {"path": path, "platform": platform, "dlcs": strs(o.get("dlcs")), "sdl": strs(o.get("sdl")),
              "language": o.get("language") if isinstance(o.get("language"), str) else "",
              "wine": o.get("wine") if isinstance(o.get("wine"), str) and platform == "Windows" else None,
              "sharedPrefix": o.get("sharedPrefix") is True}
    r = _window(heroic_dir, adapter().install_js(runner, app_name, choice), 60, "install", runner)
    if "error" in r:
        return r
    log("install:", app_name, platform, path, "->", r["value"])
    return {"result": r["value"]}


def uninstall_options(runner: str, app_name: str, heroic_dir: Path | None) -> dict:
    r = _window(heroic_dir, adapter().uninstall_options_js(runner, app_name), 60, "uninstall options", runner)
    if "error" in r:
        return r
    o = r["value"]
    return o if isinstance(o, dict) else {"error": "script", "detail": repr(o)}


def start_uninstall(runner: str, app_name: str, remove_prefix: bool, remove_settings: bool,
                    heroic_dir: Path | None) -> dict:
    r = _window(heroic_dir, adapter().uninstall_js(runner, app_name, remove_prefix, remove_settings), 15, "uninstall",
                runner)
    if "error" in r:
        return r
    log("uninstall:", app_name, "prefix" if remove_prefix else "", "settings" if remove_settings else "", "->", r["value"])
    return {"result": r["value"]}


def heroic_downloads(heroic_dir: Path | None) -> dict:
    """Heroic's download queue and latest progress, if our Heroic is running (never starts it):
    {"running": False} or {"running": True, state, queue, progress, uninstalling} (see base.py)."""
    port = _active_debug_port(heroic_dir)
    ws = _heroic_page_ws(port) if port else None
    if not ws:
        return {"running": False}
    try:
        d = _cdp_eval(ws, adapter().downloads_js(), timeout=5)
    except (OSError, RuntimeError, ValueError):
        return {"running": False}
    return {"running": True, **adapter().normalise_downloads(d)}
