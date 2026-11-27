"""Steam: the store client, non-Steam shortcuts (shortcuts.vdf) and when Steam started."""
from __future__ import annotations

import json
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config as cfg
from . import heroic as heroic_ctl
from . import util
from .config import UA
from .logs import log


# ------------------------------------------------------------------- Steam client

class Steam:
    """Tiny throttled client for Steam's public (key-less) store endpoints."""

    def __init__(self, min_interval=0.3, name="Steam"):
        self.min_interval = min_interval
        self.name = name
        self._last = 0.0
        self._paused_until = 0.0     # after a "slow down" reply, every caller waits (searches run in parallel)
        self._lock = threading.Lock()

    def get(self, url: str, params: dict, timeout=20, form: dict | None = None):
        """GET url?params (or POST form, when given), retried with back-off; returns the JSON."""
        full = url + "?" + urllib.parse.urlencode(params) if params else url
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        for attempt in range(6):
            with self._lock:
                wait = max(self._last + self.min_interval, self._paused_until) - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            req = urllib.request.Request(full, data=body, headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                e.close()   # release the error response before retrying
                if e.code in (429, 500, 502, 503, 504):
                    delay = min(90, 5 * 2 ** attempt)
                    ra = (e.headers or {}).get("Retry-After", "")
                    if ra.isdigit():  # the server says exactly how long to wait
                        delay = min(120, int(ra) + 1)
                    log(f"{self.name} HTTP {e.code}, backing off {delay}s")
                    with self._lock:   # everyone waits, this caller too (at the top of the loop)
                        self._paused_until = max(self._paused_until, time.monotonic() + delay)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as e:
                delay = 3 * (attempt + 1)
                log(f"network error ({e}), retrying in {delay}s")
                time.sleep(delay)
        raise RuntimeError(f"{self.name} request failed repeatedly: " + url)

    def search(self, term: str) -> list[dict]:
        d = self.get("https://store.steampowered.com/api/storesearch/",
                     {"term": term, "cc": "us", "l": "english"})
        return [i for i in d.get("items", []) if i.get("type") == "app"]

    def items(self, appids: list[int]) -> list[dict]:
        payload = {
            "ids": [{"appid": a} for a in appids],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {"include_reviews": True, "include_tag_count": 20,
                             "include_basic_info": True, "include_release": True},
        }
        d = self.get("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
                     {"input_json": json.dumps(payload)}, timeout=40)
        return d.get("response", {}).get("store_items", [])

    def tag_names(self) -> dict[str, str]:
        d = self.get("https://api.steampowered.com/IStoreService/GetTagList/v1/", {"language": "english"})
        return {str(t["tagid"]): t["name"] for t in d.get("response", {}).get("tags", [])}
OLD_DEBUG_SHORTCUT_NAME = "Heroic (Debug)"   # added by an earlier version of this app; removed now


def vdf_load(data: bytes) -> dict:
    """Parse Steam's binary VDF (shortcuts.vdf). Keeps key order and value types."""
    pos = 0

    def cstr() -> str:
        nonlocal pos
        end = data.index(b"\0", pos)
        s = data[pos:end].decode("utf-8", "surrogateescape")
        pos = end + 1
        return s

    def node() -> dict:
        nonlocal pos
        out = {}
        while True:
            t = data[pos]
            pos += 1
            if t == 0x08:
                return out
            k = cstr()
            if t == 0x00:
                out[k] = node()
            elif t == 0x01:
                out[k] = cstr()
            elif t == 0x02:
                out[k] = ("int", int.from_bytes(data[pos:pos + 4], "little"))
                pos += 4
            else:
                raise ValueError(f"unsupported VDF type {t:#x}")

    return node()


def vdf_dump(obj: dict) -> bytes:
    out = bytearray()
    for k, v in obj.items():
        kb = k.encode("utf-8", "surrogateescape") + b"\0"
        if isinstance(v, dict):
            out += b"\x00" + kb + vdf_dump(v)
        elif isinstance(v, tuple):
            out += b"\x02" + kb + v[1].to_bytes(4, "little")
        else:
            out += b"\x01" + kb + v.encode("utf-8", "surrogateescape") + b"\0"
    return bytes(out) + b"\x08"


def _field(entry: dict, name: str):
    for k, v in entry.items():
        if k.lower() == name.lower():
            return v
    return None


def shortcut_files() -> list[Path]:
    """Each Steam user's shortcuts.vdf (deduplicated: ~/.steam/steam usually links to ~/.local/share/Steam)."""
    files, seen = [], set()
    for root in cfg.STEAM_USERDATA:
        for conf_dir in sorted(root.glob("*/config")):
            f = conf_dir / "shortcuts.vdf"
            if conf_dir.resolve() not in seen:
                seen.add(conf_dir.resolve())
                files.append(f)
    return files


def remove_old_debug_shortcut() -> list[str]:
    """Remove the hidden "Heroic (Debug)" shortcut an earlier version of this app added."""
    changed = []
    for f in shortcut_files():
        try:
            data = f.read_bytes()
            root = vdf_load(data)
        except (OSError, ValueError, IndexError):
            continue
        sc = root.get("shortcuts", {})
        keep = [v for v in sc.values() if not (isinstance(v, dict) and (_field(v, "AppName") or "") == OLD_DEBUG_SHORTCUT_NAME)]
        if len(keep) == len(sc):
            continue
        root["shortcuts"] = {str(i): v for i, v in enumerate(keep)}   # Steam numbers them 0, 1, 2…
        new = vdf_dump(root)
        vdf_load(new)
        f.with_name(f"shortcuts.vdf.heroic-ratings-{time.strftime('%Y%m%d-%H%M%S')}.bak").write_bytes(data)
        tmp = f.with_name("shortcuts.vdf.heroic-ratings.tmp")
        tmp.write_bytes(new)
        os.replace(tmp, f)
        changed.append(str(f))
        log("removed the old", OLD_DEBUG_SHORTCUT_NAME, "shortcut from", f)
    return changed


def _process_start(pid: int) -> float | None:
    """Start time of a process (epoch seconds) from /proc, or None."""
    try:
        btime = next(float(l.split()[1]) for l in Path("/proc/stat").read_text().splitlines() if l.startswith("btime"))
        stat = Path(f"/proc/{pid}/stat").read_text()
        return btime + int(stat.rsplit(")", 1)[1].split()[19]) / os.sysconf("SC_CLK_TCK")
    except (OSError, StopIteration, ValueError, IndexError):
        return None


def steam_start_time() -> float | None:
    """When the running Steam client started (epoch seconds), or None if it isn't running.

    Steam writes its PID to ~/.steam/steam.pid each time it starts, so that names the
    current client. (Taking the oldest process called "steam" was wrong: in Game Mode a
    session process with that name outlives "Restart Steam".)
    """
    try:
        pid = int(cfg.STEAM_PID_FILE.read_text().strip())
        if Path(f"/proc/{pid}").exists():
            return _process_start(pid) or cfg.STEAM_PID_FILE.stat().st_mtime
    except (OSError, ValueError):
        pass
    # Fallback: the newest Steam client process (its binary lives in .../ubuntu12_32/steam).
    starts = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if (d / "comm").read_text().strip() != "steam":
                continue
            if not os.readlink(d / "exe").endswith("/ubuntu12_32/steam"):
                continue
        except OSError:
            continue
        t = _process_start(int(d.name))
        if t:
            starts.append(t)
    return max(starts) if starts else None


def _load_shortcut_state() -> dict:
    try:
        return json.loads(cfg.SHORTCUT_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _shortcut_game(entry: dict) -> tuple[str, str] | None:
    """(runner, app_name) a Heroic-made game shortcut launches, from its launch URL."""
    return heroic_ctl.adapter().parse_launch((_field(entry, "Exe") or "") + " " + (_field(entry, "LaunchOptions") or ""))


def find_game_shortcut(runner: str, app_name: str) -> tuple[dict, Path] | None:
    """The game's own Steam shortcut (Heroic adds one per installed game), if any."""
    for f in shortcut_files():
        try:
            root = vdf_load(f.read_bytes()) if f.exists() else {}
        except (OSError, ValueError, IndexError):
            continue
        for entry in root.get("shortcuts", {}).values():
            if isinstance(entry, dict) and _shortcut_game(entry) == (runner, app_name):
                return entry, f
    return None


def shortcut_loaded_by_steam(entry: dict, f: Path) -> bool:
    """Has the running Steam loaded this shortcut? Steam reads shortcuts.vdf only at start-up,
    so a shortcut Heroic added since then isn't launchable through Steam until a restart.
    We note when we first see each shortcut; one first seen before Steam started is loaded."""
    started = steam_start_time()
    if not started:
        return False
    appid = str((_field(entry, "appid") or ("int", 0))[1])
    st = _load_shortcut_state()
    seen = st.setdefault("seen", {})
    if appid not in seen:
        try:
            older_than_steam = f.stat().st_mtime < started   # whole file predates this Steam session
        except OSError:
            older_than_steam = False
        seen[appid] = 0 if older_than_steam else time.time()
        try:
            cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
            cfg.SHORTCUT_STATE_FILE.write_text(json.dumps(st))
        except OSError:
            pass
    return seen[appid] < started


def note_game_shortcuts():
    """Record the game shortcuts present now (so ones added later are recognised as new)."""
    for f in shortcut_files():
        try:
            root = vdf_load(f.read_bytes()) if f.exists() else {}
        except (OSError, ValueError, IndexError):
            continue
        for entry in root.get("shortcuts", {}).values():
            if isinstance(entry, dict) and _shortcut_game(entry):
                shortcut_loaded_by_steam(entry, f)


def steam_run(gameid: int):
    url = f"steam://rungameid/{gameid}"
    util.spawn_detached(["steam", url] if shutil.which("steam") else ["xdg-open", url])


def game_shortcut_status(runner: str, app_name: str) -> str:
    """"ready" (Steam can launch it), "restart" (added since Steam started),
    "missing" (no Steam shortcut) or "steam-not-running"."""
    found = find_game_shortcut(runner, app_name)
    if not found:
        return "missing"
    if not steam_start_time():
        return "steam-not-running"
    return "ready" if shortcut_loaded_by_steam(*found) else "restart"


def shortcut_gameid(entry: dict) -> int:
    """64-bit game ID that steam://rungameid/ uses for a non-Steam shortcut."""
    return ((_field(entry, "appid") or ("int", 0))[1] << 32) | 0x02000000
