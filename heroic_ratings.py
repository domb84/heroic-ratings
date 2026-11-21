#!/usr/bin/env python3
"""Heroic Ratings - browse your Heroic Games Launcher library sorted by rating.

Pure standard-library Python (3.9+). Reads the Heroic library cache, matches every
game to Steam (free, no API key) to get user-review scores, Metascores and genre
tags, then serves a controller-friendly web UI on http://127.0.0.1:8787.

    python3 heroic_ratings.py            # server only, open the URL yourself
    python3 heroic_ratings.py --kiosk    # also open a fullscreen browser (Game Mode)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path(os.environ.get("HEROIC_RATINGS_DATA", APP_DIR / "data"))
CACHE_FILE = DATA_DIR / "cache.json"
OVERRIDES_FILE = DATA_DIR / "overrides.json"
HOME = Path.home()
HEROIC_FLATPAK_ID = "com.heroicgameslauncher.hgl"
HEROIC_DIRS = [
    HOME / ".var/app" / HEROIC_FLATPAK_ID / "config/heroic",
    HOME / ".config/heroic",
]

STORE_LABELS = {"legendary": "Epic", "gog": "GOG", "nile": "Amazon", "sideload": "Other"}
LIBRARY_FILES = [
    ("store_cache/legendary_library.json", "legendary"),
    ("store_cache/gog_library.json", "gog"),
    ("store_cache/nile_library.json", "nile"),
    ("sideload_apps/library.json", "sideload"),
    ("gog_store/library.json", "gog"),  # older Heroic versions
]

MATCH_THRESHOLD = 0.88
STEAM_REFRESH_SECS = 7 * 86400      # re-fetch review scores weekly
NOMATCH_RETRY_SECS = 30 * 86400     # retry games not found on Steam monthly
TAGLIST_REFRESH_SECS = 30 * 86400

# Steam user tags that describe a genre (used for the genre filter).
GENRE_TAGS = [
    "Action", "Adventure", "RPG", "Strategy", "Simulation", "Puzzle", "Platformer",
    "Shooter", "FPS", "Third-Person Shooter", "Roguelike", "Roguelite", "Metroidvania",
    "Survival", "Horror", "Survival Horror", "Racing", "Sports", "Fighting", "Open World",
    "Souls-like", "Action RPG", "JRPG", "CRPG", "Tactical RPG", "Turn-Based Strategy",
    "Turn-Based Tactics", "RTS", "Grand Strategy", "4X", "City Builder", "Colony Sim",
    "Management", "Base Building", "Tower Defense", "Card Game", "Deckbuilding",
    "Card Battler", "Visual Novel", "Point & Click", "Stealth", "Hack and Slash",
    "Beat 'em up", "Bullet Hell", "Shoot 'Em Up", "Twin Stick Shooter", "Arcade",
    "Casual", "Sandbox", "Crafting", "Exploration", "Walking Simulator",
    "Story Rich", "Immersive Sim", "Party Game", "MMORPG", "Battle Royale", "MOBA",
    "Farming Sim", "Life Sim", "Space", "Driving", "Flight", "Rhythm", "Mystery",
    "Detective", "Interactive Fiction", "Narrative", "Hidden Object", "Indie",
    "Free to Play", "Co-op", "Online Co-Op", "Local Co-Op", "Multiplayer",
    "Puzzle Platformer", "Precision Platformer", "3D Platformer", "2D Platformer",
    "Dungeon Crawler", "Looter Shooter", "Automation", "Economy", "Wargame",
    "Psychological Horror", "Action-Adventure",
]
GENRE_SET = set(GENRE_TAGS)

UA = "heroic-ratings/1.0 (local library tool)"


# --------------------------------------------------------------------------- utils

def log(*a):
    print("[heroic-ratings]", *a, file=sys.stderr, flush=True)


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# --------------------------------------------------------------- Heroic library

def find_heroic_dir(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None
    for d in HEROIC_DIRS:
        if (d / "store_cache").is_dir() or (d / "gog_store").is_dir():
            return d
    return None


def _iter_entries(obj):
    """Yield game-like dicts (having app_name + title) without descending into them."""
    if isinstance(obj, dict):
        if "app_name" in obj and "title" in obj:
            yield obj
            return
        for v in obj.values():
            yield from _iter_entries(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_entries(v)


def _installed_ids(heroic: Path) -> set[str]:
    ids: set[str] = set()
    leg = read_json(heroic / "legendaryConfig/legendary/installed.json", {})
    if isinstance(leg, dict):
        ids.update(f"legendary:{k}" for k in leg)
    gog = read_json(heroic / "gog_store/installed.json", {})
    if isinstance(gog, dict):
        for g in gog.get("installed", []):
            if isinstance(g, dict) and g.get("appName"):
                ids.add(f"gog:{g['appName']}")
    nile = read_json(heroic / "nile_config/nile/installed.json", [])
    if isinstance(nile, list):
        for g in nile:
            if isinstance(g, dict) and g.get("id"):
                ids.add(f"nile:{g['id']}")
    return ids


def _legendary_metadata_fallback(heroic: Path):
    """Old/missing store cache: read raw Epic metadata written by legendary."""
    meta_dir = heroic / "legendaryConfig/legendary/metadata"
    if not meta_dir.is_dir():
        return
    for f in meta_dir.glob("*.json"):
        m = read_json(f, {})
        md = m.get("metadata") or {}
        if not m.get("app_name") or md.get("mainGameItem"):  # skip DLC
            continue
        art = ""
        for img in md.get("keyImages", []):
            if img.get("type") in ("DieselGameBoxTall", "DieselGameBox"):
                art = img.get("url", "")
                if img.get("type") == "DieselGameBoxTall":
                    break
        yield {"app_name": m["app_name"], "title": m.get("app_title") or md.get("title", m["app_name"]),
               "runner": "legendary", "art_square": art}


def load_library(heroic: Path) -> list[dict]:
    installed = _installed_ids(heroic)
    games: dict[str, dict] = {}
    seen_runners = set()

    def add(e, default_runner):
        runner = e.get("runner") or default_runner
        app = str(e.get("app_name"))
        title = str(e.get("title") or app).strip()
        if not app or not title or e.get("install", {}).get("is_dlc"):
            return
        gid = f"{runner}:{app}"
        if gid in games:
            return
        seen_runners.add(runner)
        games[gid] = {
            "id": gid,
            "app_name": app,
            "runner": runner,
            "store": STORE_LABELS.get(runner, runner.title()),
            "title": title,
            "installed": bool(e.get("is_installed")) or gid in installed,
            "art": e.get("art_square") or e.get("art_cover") or "",
            "art_wide": e.get("art_cover") or "",
        }

    for rel, runner in LIBRARY_FILES:
        data = read_json(heroic / rel)
        if data is None:
            continue
        for e in _iter_entries(data):
            add(e, runner)
    if "legendary" not in seen_runners:
        for e in _legendary_metadata_fallback(heroic):
            add(e, "legendary")
    return sorted(games.values(), key=lambda g: g["title"].lower())


# ----------------------------------------------------------------- title matching

ROMAN = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii"}
ROMAN_DIGITS = {r: str(n) for n, r in enumerate(
    ["", "", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii"]) if r}
EDITION_RE = re.compile(
    r"\s+(?:the\s+)?(?:game of the year|goty|definitive|complete|deluxe|digital deluxe|super deluxe|"
    r"ultimate|enhanced|special|standard|gold|premium|anniversary|extended|legendary|collector s|"
    r"collectors|royal|maximum|platinum|director s|directors|final|remastered|complete season|celebration|"
    r"\d+ year celebration|digital|hd)"
    r"\s+(?:edition|cut|bundle|version)$"
)
TRAIL_RE = re.compile(
    r"\s+(?:goty|game of the year|remastered|remaster|enhanced|legacy|redux|complete|definitive|"
    r"\d+ year celebration|pc|windows|epic games store|epic edition|hd)$")
# A subtitle that only names an edition, e.g. "X: Spacer's Choice Edition", "X - 20 Year Celebration".
SUBTITLE_SPLIT = re.compile(r"\s*(?::|\s[-–—]\s)\s*")
EDITIONISH = re.compile(r"\b(?:edition|celebration|cut|remaster(?:ed)?|collection|goty|game of the year|"
                        r"complete|anniversary|bundle|deluxe|ultimate|definitive|enhanced)\b", re.I)
JUNK = ("soundtrack", " ost", "demo", "playtest", "artbook", "art book", "season pass",
        "expansion pass", "dlc", "upgrade", "pack", "costume", "wallpaper", "dedicated server", "sdk")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    # "III" == "3" (lone "i" is skipped: too ambiguous)
    return " ".join(ROMAN_DIGITS.get(w, w) for w in s.split())


def strip_edition(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = EDITION_RE.sub("", s)
        s = TRAIL_RE.sub("", s)
    return s.strip()


def _numbers(s: str) -> set[str]:
    return {t for t in s.split() if t.isdigit() or t in ROMAN}


def base_title(raw: str) -> str:
    """Drop a trailing subtitle if it only describes an edition."""
    parts = SUBTITLE_SPLIT.split(raw)
    if len(parts) > 1 and EDITIONISH.search(parts[-1]):
        return strip_edition(norm(" ".join(parts[:-1])))
    return ""


def similarity(title: str, candidate: str) -> float:
    a, b = norm(title), norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a2, b2 = strip_edition(a), strip_edition(b)
    if a2 == b2:
        return 0.97
    if a2.replace(" ", "") == b2.replace(" ", ""):
        return 0.95
    fa = {a2, base_title(title)} - {""}
    fb = {b2, base_title(candidate)} - {""}
    if fa & fb:
        return 0.94
    r = SequenceMatcher(None, a2, b2).ratio()
    if _numbers(a2) != _numbers(b2):
        r -= 0.25
    bl, al = " " + b, " " + a
    for w in JUNK:
        if w in bl and w not in al:
            r -= 0.3
    return r


def _clean_query(t: str) -> str:
    # Steam search treats " - " as an exclusion operator, so strip punctuation.
    t = re.sub(r"[™®©]", "", t)
    t = re.sub(r"[:\-–—+/|()\[\]]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def search_queries(title: str) -> list[str]:
    qs = []
    parts = SUBTITLE_SPLIT.split(title)
    if len(parts) > 1 and EDITIONISH.search(parts[-1]):
        qs.append(" ".join(parts[:-1]))
    qs += [title, strip_edition(norm(title))]
    if len(parts) > 1 and len(parts[0]) >= 4:
        qs.append(parts[0])
    out = []
    for q in qs:
        q = _clean_query(q)
        if q and q.lower() not in (o.lower() for o in out):
            out.append(q)
    return out


# ------------------------------------------------------------------- Steam client

class Steam:
    """Tiny throttled client for Steam's public (key-less) store endpoints."""

    def __init__(self, min_interval=0.3):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def get(self, url: str, params: dict, timeout=20):
        full = url + "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            with self._lock:
                wait = self._last + self.min_interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    delay = min(90, 5 * 2 ** attempt)
                    log(f"Steam HTTP {e.code}, backing off {delay}s")
                    time.sleep(delay)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError) as e:
                delay = 3 * (attempt + 1)
                log(f"network error ({e}), retrying in {delay}s")
                time.sleep(delay)
        raise RuntimeError("Steam request failed repeatedly: " + url)

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


def best_match(steam: Steam, title: str):
    """Return (appid, steam_name, score, metascore) or (None, '', best_score, None)."""
    best = (None, "", 0.0, None)
    for q in search_queries(title):
        for item in steam.search(q):
            s = similarity(title, item.get("name", ""))
            if s > best[2]:
                ms = item.get("metascore")
                best = (item.get("id"), item.get("name", ""), s,
                        int(ms) if str(ms or "").isdigit() and int(ms) > 0 else None)
        if best[2] >= MATCH_THRESHOLD:
            break
    if best[2] < MATCH_THRESHOLD:
        return None, best[1], best[2], None
    return best


def weighted_rating(percent: float, count: int) -> float:
    """SteamDB-style rating: pulls low-review-count games toward 50%."""
    avg = percent / 100.0
    return 100.0 * (avg - (avg - 0.5) * 2 ** (-math.log10(count + 1)))


# ------------------------------------------------------------------- app state

class State:
    def __init__(self, heroic_dir: str | None):
        self.heroic_arg = heroic_dir
        self.lock = threading.RLock()
        self.steam = Steam()
        self.cache = read_json(CACHE_FILE, {}) or {}
        self.cache.setdefault("matches", {})
        self.cache.setdefault("steam", {})
        self.cache.setdefault("tags", {})
        self.games: list[dict] = []
        self.heroic: Path | None = None
        self.progress = {"phase": "starting", "done": 0, "total": 0, "message": ""}
        self.wake = threading.Event()
        self.force = False
        self.version = 0  # bumps when data changes so the UI can refresh

    # -- persistence
    def save(self):
        with self.lock:
            write_json_atomic(CACHE_FILE, self.cache)

    def overrides(self) -> dict:
        return read_json(OVERRIDES_FILE, {}) or {}

    def set_override(self, gid: str, appid):
        with self.lock:
            ov = self.overrides()
            ov[gid] = appid
            write_json_atomic(OVERRIDES_FILE, ov)
            self.cache["matches"].pop(gid, None)
            self.version += 1
        self.wake.set()

    # -- library
    def reload_library(self):
        heroic = find_heroic_dir(self.heroic_arg)
        games = load_library(heroic) if heroic else []
        with self.lock:
            self.heroic, self.games = heroic, games
            self.version += 1
        log(f"library: {len(games)} games from {heroic}")

    def _library_signature(self):
        if not self.heroic:
            return None
        files = [rel for rel, _ in LIBRARY_FILES] + [
            "legendaryConfig/legendary/installed.json", "gog_store/installed.json",
            "nile_config/nile/installed.json"]
        sig = []
        for rel in files:
            try:
                sig.append((rel, (self.heroic / rel).stat().st_mtime_ns))
            except OSError:
                pass
        return tuple(sig)

    def maybe_reload_library(self):
        """Pick up installs/uninstalls/new games made in Heroic (checked at most every 5 s)."""
        now = time.monotonic()
        if now - getattr(self, "_lib_checked", 0) < 5:
            return
        self._lib_checked = now
        sig = self._library_signature()
        prev = getattr(self, "_lib_sig", None)
        if prev is None:
            self._lib_sig = sig
            return
        # While a sync runs, leave the old signature so the change is picked up afterwards.
        if sig == prev or self.progress.get("phase") in ("starting", "matching", "ratings"):
            return
        self._lib_sig = sig
        log("Heroic library changed on disk, reloading")
        self.reload_library()
        if any(g["id"] not in self.cache["matches"] for g in self.games):
            self.wake.set()  # new games: fetch their ratings

    # -- background sync
    def set_progress(self, phase, done=0, total=0, message=""):
        with self.lock:
            self.progress = {"phase": phase, "done": done, "total": total, "message": message}
            self.version += 1

    def sync(self):
        self.reload_library()
        if not self.heroic:
            self.set_progress("error", message="Heroic config folder not found. Use --heroic-dir.")
            return
        now = time.time()
        force, self.force = self.force, False
        ov = self.overrides()
        matches = self.cache["matches"]

        # 1. title -> Steam appid
        todo = []
        for g in self.games:
            m = matches.get(g["id"])
            if g["id"] in ov:
                if not m or m.get("appid") != ov[g["id"]] or not m.get("override"):
                    matches[g["id"]] = {"title": g["title"], "appid": ov[g["id"]], "name": "",
                                        "score": 1.0, "metascore": None, "override": True, "t": now}
                continue
            if (force or not m or m.get("title") != g["title"]
                    or (m.get("appid") is None and now - m.get("t", 0) > NOMATCH_RETRY_SECS)):
                todo.append(g)
        for i, g in enumerate(todo):
            self.set_progress("matching", i, len(todo), g["title"])
            try:
                appid, name, score, meta = best_match(self.steam, g["title"])
            except Exception as e:  # network down etc.: keep what we have
                log("match failed:", g["title"], e)
                self.set_progress("error", i, len(todo), f"Steam unreachable: {e}")
                self.save()
                return
            matches[g["id"]] = {"title": g["title"], "appid": appid, "name": name,
                                "score": round(score, 3), "metascore": meta, "t": now}
            if i % 20 == 19:
                self.save()
        self.save()

        # 2. tag names
        if force or not self.cache["tags"] or now - self.cache.get("tags_t", 0) > TAGLIST_REFRESH_SECS:
            try:
                self.cache["tags"] = self.steam.tag_names()
                self.cache["tags_t"] = now
            except Exception as e:
                log("tag list failed:", e)

        # 3. reviews/tags per appid, batched
        sdata = self.cache["steam"]
        appids = sorted({m["appid"] for g in self.games
                         if (m := matches.get(g["id"])) and m.get("appid")})
        stale = [a for a in appids if force or str(a) not in sdata
                 or now - sdata[str(a)].get("t", 0) > STEAM_REFRESH_SECS]
        for i in range(0, len(stale), 100):
            batch = stale[i:i + 100]
            self.set_progress("ratings", i, len(stale), "Fetching Steam reviews")
            try:
                items = self.steam.items(batch)
            except Exception as e:
                log("GetItems failed:", e)
                self.set_progress("error", i, len(stale), f"Steam unreachable: {e}")
                self.save()
                return
            for it in items:
                aid = it.get("appid") or it.get("id")
                if not aid:
                    continue
                rev = (it.get("reviews") or {}).get("summary_filtered") or {}
                basic = it.get("basic_info") or {}
                rel = (it.get("release") or {}).get("steam_release_date")
                sdata[str(aid)] = {
                    "t": now,
                    "ok": it.get("success") == 1,
                    "percent": rev.get("percent_positive"),
                    "count": rev.get("review_count", 0),
                    "label": rev.get("review_score_label", ""),
                    "tagids": [t["tagid"] for t in sorted(it.get("tags") or [],
                                                          key=lambda t: -t.get("weight", 0))],
                    "desc": basic.get("short_description", ""),
                    "devs": [d.get("name") for d in basic.get("developers", []) if d.get("name")],
                    "year": time.gmtime(int(rel)).tm_year if rel else None,
                    "name": it.get("name", ""),
                }
            for a in batch:  # remember appids Steam returned nothing for
                sdata.setdefault(str(a), {"t": now, "ok": False})
            self.save()
        with self.lock:
            self.version += 1
        self.set_progress("done", len(self.games), len(self.games))

    def worker(self):
        while True:
            try:
                self.sync()
            except Exception as e:  # never let the thread die
                log("sync crashed:", repr(e))
                self.set_progress("error", message=str(e))
            self.wake.wait()
            self.wake.clear()

    # -- API view
    def game_view(self, g: dict) -> dict:
        m = self.cache["matches"].get(g["id"]) or {}
        out = dict(g)
        appid = m.get("appid")
        out.update({"appid": appid, "steam_name": m.get("name", ""), "match_score": m.get("score"),
                    "metascore": m.get("metascore"), "override": bool(m.get("override")),
                    "matched": g["id"] in self.cache["matches"]})
        s = self.cache["steam"].get(str(appid)) if appid else None
        tags_map = self.cache["tags"]
        if s and s.get("ok"):
            names = [tags_map.get(str(t)) for t in s.get("tagids", [])]
            names = [n for n in names if n]
            pct, cnt = s.get("percent"), s.get("count") or 0
            out.update({
                "percent": pct, "reviews": cnt, "label": s.get("label", ""),
                "rating": round(weighted_rating(pct, cnt), 1) if pct is not None and cnt else None,
                "genres": [n for n in names if n in GENRE_SET][:8],
                "tags": names[:12], "desc": s.get("desc", ""), "devs": s.get("devs", []),
                "year": s.get("year"),
            })
            if not out["steam_name"]:
                out["steam_name"] = s.get("name", "")
        if not out.get("art") and appid:
            out["art"] = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg"
        out.setdefault("genres", [])
        out.setdefault("tags", [])
        return out

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "version": self.version,
                "progress": dict(self.progress),
                "heroic_dir": str(self.heroic) if self.heroic else None,
                "games": [self.game_view(g) for g in self.games],
            }


# ------------------------------------------------------------------- launching

def heroic_command(runner: str, app_name: str, show_gui: bool = False) -> list[str]:
    """Build a heroic://launch command.

    For an installed game Heroic launches it straight away (window kept hidden).
    For a game that isn't installed, Heroic itself asks "do you wish to install it?"
    and then opens its install dialog, so the same URL doubles as "install".
    """
    params = {"appName": app_name, "runner": runner}
    if not show_gui:
        params["gui"] = "false"
    url = "heroic://launch?" + urllib.parse.urlencode(params)
    tmpl = os.environ.get("HEROIC_LAUNCH_CMD")
    if tmpl:
        return [p.replace("{url}", url) for p in tmpl.split()]
    flags = ["--no-sandbox"] if show_gui else ["--no-gui", "--no-sandbox"]
    if shutil.which("flatpak") and (HOME / ".var/app" / HEROIC_FLATPAK_ID).is_dir():
        return ["flatpak", "run", HEROIC_FLATPAK_ID, *flags, url]
    if shutil.which("heroic"):
        return ["heroic", *flags, url]
    return ["xdg-open", url]


def spawn_detached(cmd: list[str]):
    log("running:", " ".join(cmd))
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ------------------------------------------------------------------- HTTP server

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


def make_handler(state: State, on_quit):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/games":
                return self._json(state.snapshot())
            if path == "/api/status":
                state.maybe_reload_library()
                with state.lock:
                    return self._json({"app": "heroic-ratings", "version": state.version,
                                       "progress": state.progress})
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (WEB_DIR / rel).resolve()
            if WEB_DIR.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, f.read_bytes(), CONTENT_TYPES.get(f.suffix, "application/octet-stream"))

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if path in ("/api/launch", "/api/install"):
                gid = body.get("id", "")
                g = next((g for g in state.games if g["id"] == gid), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                try:
                    spawn_detached(heroic_command(g["runner"], g["app_name"],
                                                  show_gui=path == "/api/install"))
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                return self._json({"ok": True})
            if path == "/api/refresh":
                state.force = bool(body.get("force"))
                state.wake.set()
                return self._json({"ok": True})
            if path == "/api/override":
                appid = body.get("appid")
                state.set_override(body.get("id", ""), int(appid) if appid else None)
                return self._json({"ok": True})
            if path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=on_quit, daemon=True).start()
                return
            self._json({"error": "not found"}, 404)

    return Handler


# ------------------------------------------------------------------- kiosk browser

CHROMIUM_FLATPAKS = ["org.chromium.Chromium", "com.google.Chrome", "com.brave.Browser",
                     "com.microsoft.Edge", "io.github.ungoogled_software.ungoogled_chromium"]
FIREFOX_FLATPAKS = ["org.mozilla.firefox", "one.ablaze.floorp", "io.gitlab.librewolf-community"]
CHROMIUM_BINS = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "brave", "microsoft-edge"]
FIREFOX_BINS = ["firefox", "floorp", "librewolf"]


def _flatpak_apps() -> set[str]:
    if not shutil.which("flatpak"):
        return set()
    try:
        out = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                             capture_output=True, text=True, timeout=15).stdout
        return set(out.split())
    except (OSError, subprocess.SubprocessError):
        return set()


def browser_command(url: str) -> list[str] | None:
    custom = os.environ.get("HEROIC_RATINGS_BROWSER")
    if custom:
        return custom.split() + [url]
    chromium_flags = ["--kiosk", "--no-first-run", "--no-default-browser-check",
                      "--disable-features=Translate,MediaRouter", "--password-store=basic",
                      "--disable-session-crashed-bubble", "--noerrdialogs"]
    apps = _flatpak_apps()
    for app in CHROMIUM_FLATPAKS:
        if app in apps:
            prof = HOME / ".var/app" / app / "data/heroic-ratings-kiosk"
            return ["flatpak", "run", app, f"--user-data-dir={prof}", *chromium_flags, url]
    for b in CHROMIUM_BINS:
        if shutil.which(b):
            prof = DATA_DIR / "browser-profile"
            return [b, f"--user-data-dir={prof}", *chromium_flags, url]
    for app in FIREFOX_FLATPAKS:
        if app in apps:
            prof = HOME / ".var/app" / app / "data/heroic-ratings-kiosk"
            prof.mkdir(parents=True, exist_ok=True)
            return ["flatpak", "run", app, "--new-instance", "--profile", str(prof), "--kiosk", url]
    for b in FIREFOX_BINS:
        if shutil.which(b):
            prof = DATA_DIR / "firefox-profile"
            prof.mkdir(parents=True, exist_ok=True)
            return [b, "--new-instance", "--profile", str(prof), "--kiosk", url]
    return None


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return json.loads(r.read()).get("app") == "heroic-ratings"
    except Exception:
        return False


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("HEROIC_RATINGS_PORT", 8787)))
    ap.add_argument("--heroic-dir", help="Heroic config dir (auto-detected for Flatpak and native)")
    ap.add_argument("--kiosk", action="store_true", help="open a fullscreen browser; exit when it closes")
    ap.add_argument("--windowed", action="store_true", help=argparse.SUPPRESS)  # used by the Electron shell
    ap.add_argument("--no-sync", action="store_true", help="don't fetch from Steam (use cache only)")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    browser = None
    stop = threading.Event()

    def quit_all():
        time.sleep(0.3)
        if browser and browser.poll() is None:
            try:
                os.killpg(browser.pid, signal.SIGTERM)
            except OSError:
                pass
        stop.set()

    server = None
    if already_running(args.port):
        log(f"server already running on {url}")
        if not args.kiosk:
            return
    else:
        state = State(args.heroic_dir)
        if args.no_sync:
            state.reload_library()
            state.set_progress("done")
        else:
            threading.Thread(target=state.worker, daemon=True).start()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(state, quit_all))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log(f"serving on {url}")

    if args.kiosk:
        cmd = browser_command(url)
        if not cmd:
            log("no supported browser found; open", url, "manually")
        else:
            log("browser:", " ".join(cmd))
            browser = subprocess.Popen(cmd, start_new_session=True)
            threading.Thread(target=lambda: (browser.wait(), stop.set()), daemon=True).start()

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    if browser and browser.poll() is None:
        try:
            os.killpg(browser.pid, signal.SIGTERM)
        except OSError:
            pass
    if server:
        server.shutdown()


if __name__ == "__main__":
    main()
