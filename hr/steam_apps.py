"""Steam's full list of apps (needs a free Steam Web API key), for matching titles locally.

With a key, the app downloads Steam's list of games once a week (a few requests) and
matches most of the library by name without searching Steam game by game. Titles with no
single clear match still go through Steam's store search. The key is stored in
<data>/steam_key.json, readable only by you, and never shown or logged.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error

from . import config as cfg
from . import matching
from . import steam as steam_api
from . import util
from .logs import log

KEY_RE = re.compile(r"[0-9A-Fa-f]{32}")
APPLIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
APPLIST_REFRESH_SECS = 7 * 86400


# ------------------------------------------------------------------- the key
def steam_key() -> str | None:
    k = os.environ.get("HEROIC_RATINGS_STEAM_KEY")
    if not k:
        try:
            k = json.loads((cfg.DATA_DIR / "steam_key.json").read_text()).get("key")
        except (OSError, ValueError, AttributeError):
            return None
    return k.strip() if k else None


def key_settings() -> dict:
    """What the settings page may show (never the key itself)."""
    source = "env" if os.environ.get("HEROIC_RATINGS_STEAM_KEY") else \
        "file" if (cfg.DATA_DIR / "steam_key.json").exists() else None
    k = steam_key()
    return {"set": bool(k), "source": source, "hint": (k[:4] + "…" + k[-4:]) if k else ""}


def _check(key: str) -> dict:
    try:
        apps, _ = fetch_page(steam_api.Steam(min_interval=0, name="Steam key check"), key, 0, 1)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code in (401, 403):
            return {"ok": False, "error": "Steam didn't accept this key. Check it at steamcommunity.com/dev/apikey."}
        return {"ok": False, "error": f"Steam answered HTTP {e.code}. Try again later."}
    except (OSError, ValueError, RuntimeError) as e:
        return {"ok": False, "error": f"Couldn't reach Steam ({type(e).__name__}). Check you're online."}
    return {"ok": True} if apps else {"ok": False, "error": "Steam accepted the key but sent no apps."}


def save_key(key: str, check=True) -> dict:
    key = (key or "").strip()
    if not KEY_RE.fullmatch(key):
        return {"ok": False, "error": "That doesn't look like a Steam Web API key (32 letters and digits, 0-9 and A-F)."}
    if check:
        r = _check(key)
        if not r["ok"]:
            return r
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = cfg.DATA_DIR / "steam_key.json"
    tmp = f.with_name("steam_key.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"key": key}, out)
    os.replace(tmp, f)
    (cfg.DATA_DIR / "steam_applist.json").unlink(missing_ok=True)    # fetch the list with the new key
    log("Steam Web API key saved")
    return {"ok": True, **key_settings()}


def remove_key() -> dict:
    if key_settings()["source"] == "env":
        return {"ok": False, "error": "This key comes from an environment variable; remove it there."}
    (cfg.DATA_DIR / "steam_key.json").unlink(missing_ok=True)
    log("Steam Web API key removed")
    return {"ok": True, **key_settings()}


def test_key() -> dict:
    k = steam_key()
    return _check(k) if k else {"ok": False, "error": "No Steam Web API key is set."}


# ------------------------------------------------------------------- the app list
def fetch_page(http: steam_api.Steam, key: str, last_appid: int, max_results: int = 50000) -> tuple[list[dict], int | None]:
    """One page of Steam's games: ([{appid, name}], the next last_appid or None at the end)."""
    d = http.get(APPLIST_URL, {"key": key, "include_games": "true", "include_dlc": "false", "include_software": "false",
                               "include_videos": "false", "include_hardware": "false",
                               "last_appid": last_appid, "max_results": max_results}, timeout=60)
    r = d.get("response") or {}
    apps = [{"appid": a["appid"], "name": a.get("name", "")} for a in r.get("apps") or [] if a.get("appid")]
    return apps, (r.get("last_appid") if r.get("have_more_results") else None)


def app_list(http: steam_api.Steam, now: float | None = None) -> dict[int, str] | None:
    """{appid: name} of Steam's games, refreshed weekly; None without a key (or if Steam fails)."""
    key = steam_key()
    if not key:
        return None
    now = now or time.time()
    f = cfg.DATA_DIR / "steam_applist.json"
    cached = util.read_json(f, {}) or {}
    if cached.get("apps") and now - cached.get("t", 0) < APPLIST_REFRESH_SECS:
        return {int(k): v for k, v in cached["apps"].items()}
    apps, last, pages = {}, 0, 0
    try:
        while last is not None and pages < 20:
            page, last = fetch_page(http, key, last)
            pages += 1
            apps.update((a["appid"], a["name"]) for a in page)
    except Exception as e:   # keep using the old list (or search) until next time
        log("Steam app list failed:", repr(e).replace(key, "<key>"))
        return {int(k): v for k, v in cached["apps"].items()} if cached.get("apps") else None
    util.write_json_atomic(f, {"t": now, "apps": {str(k): v for k, v in apps.items()}})
    log(f"Steam app list: {len(apps)} games ({pages} requests)")
    return apps


class AppIndex:
    """Titles -> Steam app, by normalised name. Only a single unambiguous app counts."""
    def __init__(self, apps: dict[int, str]):
        self.names = apps
        self.by_name: dict[str, list[int]] = {}
        for appid, name in apps.items():
            k = matching.norm(name)
            if k:
                self.by_name.setdefault(k, []).append(appid)

    def match(self, title: str) -> tuple[int, str, float] | None:
        """(appid, Steam name, similarity), or None when there's no single clear match."""
        for q in matching.search_queries(title):
            ids = self.by_name.get(matching.norm(q))
            if not ids:
                continue
            if len(ids) > 1:          # e.g. a remake with the same name: let the store search rank them
                return None
            name = self.names[ids[0]]
            s = matching.similarity(title, name)
            return (ids[0], name, s) if s >= cfg.MATCH_THRESHOLD else None
        return None
