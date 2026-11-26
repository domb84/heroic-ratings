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
import logging
import logging.handlers
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
# IGDB scores based on very few reviews (median: 4 critics) aren't comparable with a
# Metascore or a Steam percentage, so they only count from these review counts up.
IGDB_MIN_CRITICS = 3
IGDB_MIN_USERS = 5
IGDB_GENRES = {
    "Role-playing (RPG)": "RPG", "Shooter": "Shooter", "Platform": "Platformer", "Puzzle": "Puzzle",
    "Racing": "Racing", "Sport": "Sports", "Fighting": "Fighting", "Strategy": "Strategy",
    "Real Time Strategy (RTS)": "RTS", "Turn-based strategy (TBS)": "Turn-Based Strategy",
    "Tactical": "Turn-Based Tactics", "Adventure": "Adventure", "Indie": "Indie", "Arcade": "Arcade",
    "Simulator": "Simulation", "Visual Novel": "Visual Novel", "Point-and-click": "Point & Click",
    "Card & Board Game": "Card Game", "Hack and slash/Beat 'em up": "Hack and Slash", "MOBA": "MOBA",
    "Music": "Rhythm", "Action": "Action", "Horror": "Horror", "Survival": "Survival", "Open world": "Open World",
    "Stealth": "Stealth", "Sandbox": "Sandbox",
}

UA = "heroic-ratings/1.0 (local library tool)"


# --------------------------------------------------------------------------- utils

# ------------------------------------------------------------------- logging
#
# Everything goes to stderr as before and, once setup_logging() has run, to
# <data>/logs/app.log (rotated: 1 MB x 4). health.log is written by HealthMonitor.

LOG_DIR = DATA_DIR / "logs"
logger = logging.getLogger("heroic-ratings")
logger.setLevel(logging.INFO)
logger.propagate = False
_stderr = logging.StreamHandler(sys.stderr)
_stderr.setFormatter(logging.Formatter("[heroic-ratings] %(message)s"))
logger.addHandler(_stderr)


def setup_logging(log_dir: Path | None = None, max_bytes: int = 1_000_000, backups: int = 3) -> Path | None:
    """Add the rotating app.log file handler. Returns the log file, or None if it can't be written."""
    log_dir = log_dir or LOG_DIR
    for h in [h for h in logger.handlers if isinstance(h, logging.FileHandler)]:
        logger.removeHandler(h)
        h.close()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=max_bytes,
                                                  backupCount=backups, encoding="utf-8")
    except OSError as e:
        log("can't write logs to", log_dir, e)
        return None
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s: %(message)s"))
    logger.addHandler(fh)
    return log_dir / "app.log"


def log(*a, level: int = logging.INFO):
    logger.log(level, " ".join(str(x) for x in a))


def log_error(*a, exc: BaseException | None = None):
    """An error, with the traceback of exc (or of the exception being handled)."""
    logger.error(" ".join(str(x) for x in a), exc_info=exc if exc is not None else True)


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
    r"\d+ year celebration|starter access|pc|windows|epic games store|epic edition|hd)$")
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

    def __init__(self, min_interval=0.3, name="Steam"):
        self.min_interval = min_interval
        self.name = name
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
                e.close()   # release the error response before retrying
                if e.code in (429, 500, 502, 503, 504):
                    delay = min(90, 5 * 2 ** attempt)
                    ra = (e.headers or {}).get("Retry-After", "")
                    if ra.isdigit():  # the server says exactly how long to wait
                        delay = min(120, int(ra) + 1)
                    log(f"{self.name} HTTP {e.code}, backing off {delay}s")
                    time.sleep(delay)
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


# ------------------------------------------------------------------- Wikidata fallback
# Steam's store search only finds games that are currently for sale, so delisted games
# (old Football Managers, Rocket League, ...) never match. Wikidata (free, no key) maps
# game names to Steam app IDs, delisted or not, and carries Metacritic/OpenCritic scores
# for games that aren't on Steam at all (Epic exclusives).

WD_API = "https://www.wikidata.org/w/api.php"
WD_VIDEO_GAME = "Q7889"
WD_CRITICS = {"Q150248": "Metacritic", "Q21039459": "OpenCritic"}
WD_PC = {"Q1406", "Q16338"}  # Microsoft Windows, personal computer
# Library entries that are tools/extras rather than games: don't look them up.
EXTRA_RE = re.compile(r"(?i)\b(?:editor|archiver|test(?:ing)? branch|public test(?:ing)?|playtest|"
                      r"soundtrack|ost|tech test|dedicated server|sdk|benchmark|browser|content)\b|beta\b")


def _wd_value(claim):
    return (claim.get("mainsnak") or {}).get("datavalue", {}).get("value")


def _wd_qualifier_ids(claim, prop):
    return {(q.get("datavalue") or {}).get("value", {}).get("id")
            for q in (claim.get("qualifiers") or {}).get(prop, [])}


def _wd_review_count(claim) -> int | None:
    """The "number of reviews" (P7887) qualifier of a review score, if any."""
    for snak in (claim.get("qualifiers") or {}).get("P7887", []):
        v = (snak.get("datavalue") or {}).get("value")
        try:
            return int(float(v["amount"])) if isinstance(v, dict) else None
        except (KeyError, ValueError, TypeError):
            return None
    return None


def _parse_score(v) -> int | None:
    m = re.fullmatch(r"\s*([\d.]+)\s*/\s*(100|10)\s*", str(v or ""))
    if not m:
        return None
    x = float(m.group(1)) * 100 / int(m.group(2))
    return round(x) if 0 < x <= 100 else None


# ------------------------------------------------------------------- game lengths
#
# IGDB, which needs a free Twitch developer app (client ID + secret). It fills gaps
# Steam leaves: critic and user scores, play styles and perspectives, series, similar
# games, multiplayer details, how long to beat, and covers/summaries/genres for games
# that aren't on Steam. Steam stays the main source wherever it has data.

IGDB_REFRESH_SECS = 30 * 86400
IGDB_MISS_RETRY_SECS = 14 * 86400


class IGDB:
    """IGDB's time-to-beat data (needs a free Twitch developer app: client ID + secret).
    Games are found by their Steam app ID, or by name when there's no Steam match."""
    API = "https://api.igdb.com/v4/"
    TOKEN_URL = "https://id.twitch.tv/oauth2/token"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id, client_secret
        self.token, self.token_expires = None, 0.0
        self.last = 0.0
        self.source_field = "external_game_source"   # newer name; older API used "category"

    def _get_token(self):
        q = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self.client_secret,
                                    "grant_type": "client_credentials"})
        with urllib.request.urlopen(urllib.request.Request(f"{self.TOKEN_URL}?{q}", data=b"", method="POST"),
                                    timeout=30) as r:
            j = json.load(r)
        self.token, self.token_expires = j["access_token"], time.time() + j.get("expires_in", 3600) - 300

    def query(self, endpoint: str, body: str) -> list:
        for attempt in range(2):
            if not self.token or time.time() > self.token_expires:
                self._get_token()
            wait = self.last + 0.3 - time.monotonic()      # IGDB allows 4 requests per second
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            req = urllib.request.Request(self.API + endpoint, data=body.encode(), method="POST", headers={
                "Client-ID": self.client_id, "Authorization": f"Bearer {self.token}", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.token = None
                    continue
                raise
        return []

    def steam_to_igdb(self, appids: list[int]) -> dict[int, int]:
        """{Steam app ID: IGDB game ID} for up to 500 app IDs."""
        uids = ",".join(f'"{a}"' for a in appids)
        try:
            ext = self.query("external_games", f"fields game,uid; where uid = ({uids}) & {self.source_field} = 1; limit 500;")
        except urllib.error.HTTPError as e:
            if e.code != 400 or self.source_field == "category":
                raise
            self.source_field = "category"   # older API: Steam is category 1
            ext = self.query("external_games", f"fields game,uid; where uid = ({uids}) & category = 1; limit 500;")
        return {int(r["uid"]): r["game"] for r in ext if str(r.get("uid", "")).isdigit() and r.get("game")}

    def igdb_to_steam(self, game_ids: list[int]) -> dict[int, int]:
        """{IGDB game ID: Steam app ID} for games IGDB knows a Steam page of."""
        if not game_ids:
            return {}
        ext = self.query("external_games", f"fields game,uid; where game = ({','.join(map(str, game_ids))}) "
                                           f"& {self.source_field} = 1; limit 500;")
        return {r["game"]: int(r["uid"]) for r in ext if str(r.get("uid", "")).isdigit() and r.get("game")}

    def search_name(self, title: str) -> tuple[int, str, float] | None:
        """Best IGDB match for a title (also checks IGDB's alternative names): (id, name, score)."""
        best, best_sc = None, 0.0
        for q in search_queries(title)[:2]:
            safe = q.replace('"', " ")
            for r in self.query("games", f'search "{safe}"; fields id,name,alternative_names.name; limit 10;'):
                names = [r.get("name", "")] + [a.get("name", "") for a in r.get("alternative_names") or []]
                sc = max(max(similarity(title, n), similarity(q, n)) for n in names if n) if any(names) else 0
                if sc > best_sc:
                    best, best_sc = r, sc
            if best_sc >= MATCH_THRESHOLD:
                break
        if not best or best_sc < MATCH_THRESHOLD:
            return None
        return best["id"], best.get("name", ""), round(best_sc, 3)

    DETAIL_FIELDS = ("name,rating,rating_count,aggregated_rating,aggregated_rating_count,game_modes.name,"
                     "player_perspectives.name,collections.name,franchises.name,similar_games,summary,cover.image_id,"
                     "first_release_date,genres.name,themes.name,involved_companies.developer,"
                     "involved_companies.company.name,multiplayer_modes.campaigncoop,multiplayer_modes.lancoop,"
                     "multiplayer_modes.offlinecoop,multiplayer_modes.offlinecoopmax,multiplayer_modes.offlinemax,"
                     "multiplayer_modes.onlinecoop,multiplayer_modes.onlinecoopmax,multiplayer_modes.onlinemax,"
                     "multiplayer_modes.splitscreen,videos.name,videos.video_id")

    def details(self, game_ids: list[int]) -> dict[int, dict]:
        """Everything the app uses about up to 500 games, including time to beat."""
        if not game_ids:
            return {}
        idl = ",".join(map(str, game_ids))
        rows = self.query("games", f"fields {self.DETAIL_FIELDS}; where id = ({idl}); limit 500;")
        ttb = {r["game_id"]: r for r in self.query(
            "game_time_to_beats", f"fields game_id,hastily,normally,completely; where game_id = ({idl}); limit 500;")
            if r.get("game_id")}
        names = lambda xs: [x["name"] for x in xs or [] if isinstance(x, dict) and x.get("name")]
        # 999h-style values are placeholders for endless games, not real lengths
        hrs = lambda v: round(v / 3600, 1) if v and v < 900 * 3600 else None
        out = {}
        for r in rows:
            mp = {}
            for m in r.get("multiplayer_modes") or []:
                for k, v in m.items():
                    if k == "id" or not v:
                        continue
                    mp[k] = max(mp.get(k, 0), v) if isinstance(v, int) and not isinstance(v, bool) else True
            t = ttb.get(r["id"], {})
            cover = (r.get("cover") or {}).get("image_id")
            rel = r.get("first_release_date")
            out[r["id"]] = {
                "id": r["id"], "name": r.get("name", ""),
                "user": round(r["rating"]) if r.get("rating") else None, "user_n": r.get("rating_count") or 0,
                "critic": round(r["aggregated_rating"]) if r.get("aggregated_rating") else None,
                "critic_n": r.get("aggregated_rating_count") or 0,
                "modes": names(r.get("game_modes")), "persp": names(r.get("player_perspectives")),
                "series": names(r.get("collections")) or names(r.get("franchises")),
                "similar": [x for x in r.get("similar_games") or [] if isinstance(x, int)],
                "summary": r.get("summary", ""),
                "cover": f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover}.jpg" if cover else None,
                "year": time.gmtime(rel).tm_year if rel else None,
                "devs": [c["company"]["name"] for c in r.get("involved_companies") or []
                         if c.get("developer") and (c.get("company") or {}).get("name")],
                "genres": names(r.get("genres")), "themes": names(r.get("themes")), "mp": mp,
                "main": hrs(t.get("hastily")), "extra": hrs(t.get("normally")), "comp": hrs(t.get("completely")),
                "videos": _igdb_videos(r.get("videos")),
            }
        return out

    def videos(self, game_id: int) -> list[dict]:
        rows = self.query("games", f"fields videos.name,videos.video_id; where id = {int(game_id)};")
        return _igdb_videos(rows[0].get("videos")) if rows else []


def _igdb_videos(vs) -> list[dict]:
    """IGDB's videos are YouTube IDs with a label ("Trailer", "Gameplay video", …)."""
    return [{"name": v.get("name") or "Video", "id": v["video_id"]} for v in vs or []
            if isinstance(v, dict) and re.fullmatch(r"[\w-]{6,20}", v.get("video_id") or "")]


TWITCH_ID_RE = re.compile(r"[A-Za-z0-9]{10,64}")


def twitch_settings() -> dict:
    """What the settings page may show about the Twitch/IGDB credentials (never the secret)."""
    if os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_ID") and os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_SECRET"):
        source = "env"
    elif (DATA_DIR / "twitch.json").exists():
        source = "file"
    else:
        source = None
    creds = twitch_credentials()
    return {"set": bool(creds), "source": source,
            "client_id_hint": (creds[0][:4] + "…" + creds[0][-3:]) if creds else ""}


def save_twitch_credentials(client_id: str, client_secret: str, check=True) -> dict:
    """Check the credentials with Twitch, then store them in <data>/twitch.json (readable only by you)."""
    client_id, client_secret = (client_id or "").strip(), (client_secret or "").strip()
    if not TWITCH_ID_RE.fullmatch(client_id) or not TWITCH_ID_RE.fullmatch(client_secret):
        return {"ok": False, "error": "That doesn't look like a Twitch client ID and secret (letters and digits only)."}
    if check:
        try:
            IGDB(client_id, client_secret)._get_token()
        except urllib.error.HTTPError as e:
            e.close()
            return {"ok": False, "error": f"Twitch didn't accept these credentials (HTTP {e.code}). Check the client ID and secret."}
        except (OSError, ValueError, KeyError) as e:
            return {"ok": False, "error": f"Couldn't reach Twitch to check them ({type(e).__name__}). Try again when online."}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / "twitch.json"
    tmp = f.with_name("twitch.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump({"client_id": client_id, "client_secret": client_secret}, out)
    os.replace(tmp, f)
    log("Twitch/IGDB credentials saved")
    return {"ok": True, **twitch_settings()}


def remove_twitch_credentials() -> dict:
    if twitch_settings()["source"] == "env":
        return {"ok": False, "error": "These come from environment variables; remove them there."}
    (DATA_DIR / "twitch.json").unlink(missing_ok=True)
    log("Twitch/IGDB credentials removed")
    return {"ok": True, **twitch_settings()}


def twitch_credentials() -> tuple[str, str] | None:
    """Twitch developer app credentials for IGDB, from the environment or data/twitch.json."""
    cid, secret = os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_ID"), os.environ.get("HEROIC_RATINGS_TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        try:
            j = json.loads((DATA_DIR / "twitch.json").read_text())
            cid, secret = j.get("client_id"), j.get("client_secret")
        except (OSError, ValueError):
            return None
    return (cid.strip(), secret.strip()) if cid and secret else None


class Wikidata:
    def __init__(self):
        self.http = Steam(min_interval=1.5, name="Wikidata")  # Wikidata rate-limits fast clients

    def _is_game(self, claims) -> bool:
        p31 = {(_wd_value(c) or {}).get("id") for c in claims.get("P31", [])}
        has_oc = any("Q21039459" in _wd_qualifier_ids(c, "P447") for c in claims.get("P444", []))
        return WD_VIDEO_GAME in p31 or "P1733" in claims or has_oc

    def lookup(self, title: str) -> dict | None:
        best = None
        for q in search_queries(title)[:2]:
            res = self.http.get(WD_API, {"action": "wbsearchentities", "search": q, "language": "en",
                                         "type": "item", "limit": 7, "format": "json"})
            hits = res.get("search", [])
            ids = [s["id"] for s in hits]
            if not ids:
                continue
            # Names the search matched on (covers language-neutral "mul" labels too).
            found = {s["id"]: [s.get("label", ""), (s.get("match") or {}).get("text", "")] for s in hits}
            ents = self.http.get(WD_API, {"action": "wbgetentities", "ids": "|".join(ids),
                                          "props": "claims|labels|aliases", "languages": "en|mul",
                                          "format": "json"}).get("entities", {})
            for qid in ids:
                e = ents.get(qid) or {}
                claims = e.get("claims") or {}
                if not self._is_game(claims):
                    continue
                labels = e.get("labels") or {}
                label = (labels.get("en") or labels.get("mul") or {}).get("value", "") or found[qid][0]
                aliases = e.get("aliases") or {}
                names = [label, *found[qid]] + [a["value"] for lang in ("en", "mul") for a in aliases.get(lang, [])]
                sim = max((similarity(title, n) for n in names if n), default=0.0)
                if sim >= MATCH_THRESHOLD and (best is None or sim > best[0]):
                    best = (sim, qid, label or title, claims)
            if best:
                break
        if not best:
            return None
        sim, qid, name, claims = best
        steam = [int(v) for c in claims.get("P1733", []) if str(v := _wd_value(c)).isdigit()]
        critics = {}  # source -> (score, priority, review count); prefer PC scores, then platform-less ones
        for c in claims.get("P444", []):
            score = _parse_score(_wd_value(c))
            src = next((WD_CRITICS[b] for b in _wd_qualifier_ids(c, "P447") if b in WD_CRITICS), None)
            if score is None or not src:
                continue
            plats = _wd_qualifier_ids(c, "P400")
            prio = 0 if plats & WD_PC else 1 if not plats else 2
            if src not in critics or prio < critics[src][1]:
                critics[src] = (score, prio, _wd_review_count(c))
        # OpenCritic first: Wikidata records its review count. (A Wikidata Metacritic score is
        # used as the Metascore when Steam has none, below.)
        critic = next(({"score": critics[s][0], "by": s, "n": critics[s][2]} for s in ("OpenCritic", "Metacritic")
                       if s in critics), None)
        return {"qid": qid, "name": name, "sim": round(sim, 3), "steam": steam[0] if steam else None,
                "critic": critic, "metacritic": critics["Metacritic"][0] if "Metacritic" in critics else None}


def weighted_rating(percent: float, count: int) -> float:
    """SteamDB-style rating: pulls low-review-count games toward 50%."""
    avg = percent / 100.0
    return 100.0 * (avg - (avg - 0.5) * 2 ** (-math.log10(count + 1)))


def is_steam_deck() -> bool:
    """True on Steam Deck hardware (LCD "Jupiter" / OLED "Galileo")."""
    try:
        vendor = Path("/sys/class/dmi/id/sys_vendor").read_text().strip()
        product = Path("/sys/class/dmi/id/product_name").read_text().strip()
    except OSError:
        return False
    return vendor == "Valve" and product in ("Jupiter", "Galileo")


STEAM_DECK = is_steam_deck()


def desktop_layout_likely(env=os.environ, deck: bool = STEAM_DECK) -> bool:
    """True when Steam is probably applying its Desktop controller layout to us.

    That happens on a Deck in Desktop Mode when the app wasn't started through
    Steam. Game Mode (gamescope) and Steam launches (SteamGameId set) give the
    app a gamepad layout instead.
    """
    if not deck:
        return False
    game_mode = env.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope" or "GAMESCOPE_WAYLAND_DISPLAY" in env
    via_steam = bool(env.get("SteamGameId") or env.get("SteamAppId"))
    return not game_mode and not via_steam


DESKTOP_LAYOUT = desktop_layout_likely()


# ------------------------------------------------------------------- app state

class State:
    def __init__(self, heroic_dir: str | None):
        self.heroic_arg = heroic_dir
        self.lock = threading.RLock()
        self.steam = Steam()
        self.wikidata = Wikidata()
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
        self.busy_until = 0.0  # Heroic downloading/uninstalling: the health log samples more often
        self.ops = OpQueue(run=self._run_op, status=lambda: heroic_downloads(self.heroic), is_installed=self._installed_now)

    def _run_op(self, job: dict) -> dict:
        p = job["params"]
        if job["kind"] == "install":
            return install_with_options(job["runner"], job["app_name"], p, self.heroic)
        return start_uninstall(job["runner"], job["app_name"], p.get("removePrefix") is True,
                               p.get("removeSettings") is True, self.heroic)

    def _installed_now(self, gid: str) -> bool:
        self.maybe_reload_library(force=True)
        g = next((g for g in self.games if g["id"] == gid), None)
        return bool(g and g["installed"])

    def mark_busy(self, secs: float = 120):
        self.busy_until = max(self.busy_until, time.time() + secs)

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

    def maybe_reload_library(self, force: bool = False):
        """Pick up installs/uninstalls/new games made in Heroic (checked at most every 5 s,
        or right away with force, e.g. when Heroic has just finished an install)."""
        now = time.monotonic()
        if not force and now - getattr(self, "_lib_checked", 0) < 5:
            return
        self._lib_checked = now
        sig = self._library_signature()
        prev = getattr(self, "_lib_sig", None)
        if prev is None:
            self._lib_sig = sig
            return
        # While a sync runs, leave the old signature so the change is picked up afterwards.
        if sig == prev or self.progress.get("phase") in ("starting", "matching", "wikidata", "igdb", "ratings"):
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

        # 1b. not found by Steam search: try Wikidata (delisted Steam games, critic scores)
        wd_todo = [g for g in self.games
                   if (m := matches.get(g["id"])) and not m.get("appid") and not m.get("override")
                   and not EXTRA_RE.search(g["title"])
                   and (force or now - m.get("wd_t", 0) > NOMATCH_RETRY_SECS
                        or (m.get("critic") and "n" not in m["critic"]))]
        # (critic scores saved before review counts were kept are re-checked once, however the
        # game was matched)
        wd_todo += [g for g in self.games if (m := matches.get(g["id"])) and not m.get("override")
                    and m.get("critic") and "n" not in m["critic"] and g not in wd_todo]
        for i, g in enumerate(wd_todo):
            self.set_progress("wikidata", i, len(wd_todo), g["title"])
            try:
                r = self.wikidata.lookup(g["title"])
            except Exception as e:  # optional source: skip it for now, retry next sync
                log("Wikidata lookup failed:", g["title"], e)
                break
            m = matches[g["id"]]
            m["wd_t"] = now
            if r:
                m.update({"wd": r["qid"], "wd_name": r["name"], "critic": r["critic"]})
                if r["steam"]:
                    m.update({"appid": r["steam"], "name": r["name"], "score": r["sim"], "via": "wikidata"})
                if r["metacritic"] and not m.get("metascore"):
                    m["metascore"] = r["metacritic"]
            if i % 10 == 9:
                self.save()
        self.save()

        # 1c. IGDB (with a Twitch key): find each game there; a game Steam search missed
        # gets its Steam page from IGDB when IGDB knows it (also via alternative names).
        self.sync_igdb_map(now, force)

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

        # 4. IGDB details: scores, play styles, series, similar games, lengths, covers…
        self.sync_igdb_details(now, force)
        self.set_progress("done", len(self.games), len(self.games))

    def sync_igdb_map(self, now: float, force: bool):
        creds = twitch_credentials()
        if not creds:
            return
        igdb = IGDB(*creds)
        imap = self.cache.setdefault("igdb_map", {})
        matches = self.cache["matches"]

        def stale(g):
            e, m = imap.get(g["id"]), matches.get(g["id"]) or {}
            if force or not e or e.get("appid") != m.get("appid"):
                return True
            return now - e.get("t", 0) > (IGDB_MISS_RETRY_SECS if not e.get("id") else IGDB_REFRESH_SECS)

        todo = [g for g in self.games if not EXTRA_RE.search(g["title"]) and stale(g)]
        if not todo:
            return
        try:
            with_appid = [g for g in todo if (matches.get(g["id"]) or {}).get("appid")]
            for i in range(0, len(with_appid), 200):
                batch = with_appid[i:i + 200]
                self.set_progress("igdb", i, len(todo), "Matching on IGDB")
                found = igdb.steam_to_igdb([matches[g["id"]]["appid"] for g in batch])
                for g in batch:
                    a = matches[g["id"]]["appid"]
                    imap[g["id"]] = {"id": found.get(a), "appid": a, "t": now}
            rest = [g for g in todo if g not in with_appid]
            for k, g in enumerate(rest):
                self.set_progress("igdb", len(with_appid) + k, len(todo), g["title"])
                r = igdb.search_name(g["title"])
                imap[g["id"]] = {"id": r[0] if r else None, "appid": None, "t": now,
                                 "name": r[1] if r else "", "score": r[2] if r else 0}
            # Steam pages for games Steam search missed, from IGDB's own links
            want = {imap[g["id"]]["id"]: g for g in rest
                    if imap[g["id"]]["id"] and not (matches.get(g["id"]) or {}).get("override")}
            ids = list(want)
            for i in range(0, len(ids), 200):
                for gid_igdb, appid in igdb.igdb_to_steam(ids[i:i + 200]).items():
                    g = want[gid_igdb]
                    m = matches.setdefault(g["id"], {"title": g["title"]})
                    m.update({"appid": appid, "name": imap[g["id"]]["name"], "score": imap[g["id"]]["score"], "via": "igdb"})
                    imap[g["id"]]["appid"] = appid
                    log("IGDB linked", g["title"], "to Steam app", appid)
        except Exception as e:   # optional source: keep what we have, try again next sync
            log("IGDB matching failed:", repr(e))
        self.save()

    def sync_igdb_details(self, now: float, force: bool):
        creds = twitch_credentials()
        if not creds:
            return
        imap, details = self.cache.get("igdb_map", {}), self.cache.setdefault("igdb", {})
        ids = sorted({e["id"] for e in imap.values() if e.get("id")})
        stale = [i for i in ids if force or str(i) not in details
                 or now - details[str(i)].get("t", 0) > IGDB_REFRESH_SECS]
        if not stale:
            return
        igdb = IGDB(*creds)
        try:
            for i in range(0, len(stale), 200):
                batch = stale[i:i + 200]
                self.set_progress("igdb", i, len(stale), "Fetching IGDB details")
                got = igdb.details(batch)
                for gid_igdb in batch:
                    details[str(gid_igdb)] = {**got.get(gid_igdb, {"id": gid_igdb, "missing": True}), "t": now}
                self.save()
        except Exception as e:
            log("IGDB details failed:", repr(e))
        self.save()
        with self.lock:
            self.version += 1

    def worker(self):
        while True:
            try:
                self.sync()
            except Exception as e:  # never let the thread die
                log_error("sync crashed:", repr(e), exc=e)
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
        ig = self._add_igdb(g, out)
        self._pick_critic(out, m.get("critic"), ig)
        out["via"] = m.get("via")
        out["extra"] = bool(EXTRA_RE.search(g["title"]))
        if not out.get("art") and appid:
            out["art"] = f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg"
        out.setdefault("genres", [])
        out.setdefault("tags", [])
        return out

    def _add_igdb(self, g: dict, out: dict):
        """IGDB data for one game: fills gaps only, Steam stays first."""
        e = self.cache.get("igdb_map", {}).get(g["id"]) or {}
        ig = self.cache.get("igdb", {}).get(str(e.get("id"))) if e.get("id") else None
        if not ig or ig.get("missing"):
            return None
        idx = getattr(self, "_igdb_index", {})
        mine = lambda ids: [x for i in ids for x in idx.get("by_id", {}).get(i, []) if x != g["id"]]
        out["igdb"] = {
            "name": ig.get("name"), "user": ig.get("user"), "user_n": ig.get("user_n"),
            "critic": ig.get("critic"), "critic_n": ig.get("critic_n"), "mp": ig.get("mp") or {},
            "series": ig.get("series") or [],
            "same_series": sorted({x for s_ in ig.get("series") or [] for x in idx.get("by_series", {}).get(s_, []) if x != g["id"]}),
            "similar": mine(ig.get("similar") or [])[:8],
        }
        out["modes"] = ig.get("modes") or []
        out["persp"] = ig.get("persp") or []
        sane = lambda v: v if v and v < 900 else None   # 999h-style placeholders (also in data saved earlier)
        if sane(ig.get("main")) or sane(ig.get("extra")) or sane(ig.get("comp")):
            out["length"] = {"main": sane(ig.get("main")), "extra": sane(ig.get("extra")), "comp": sane(ig.get("comp")), "src": "igdb"}
        # games without Steam data: fill the blanks from IGDB
        if not out.get("desc"):
            out["desc"] = ig.get("summary", "")
        if not out.get("year"):
            out["year"] = ig.get("year")
        if not out.get("devs"):
            out["devs"] = ig.get("devs") or []
        if not out.get("genres"):
            out["genres"] = [IGDB_GENRES[x] for x in (ig.get("genres") or []) + (ig.get("themes") or []) if x in IGDB_GENRES][:8]
            out["tags"] = list(dict.fromkeys(out["genres"] + (ig.get("themes") or [])))[:12]
        if not out.get("art") and ig.get("cover") and not out.get("appid"):   # Steam's art first
            out["art"] = ig["cover"]
        return ig

    @staticmethod
    def _pick_critic(out: dict, wd: dict | None, ig: dict | None):
        """Choose the critic score (IGDB vs Wikidata's OpenCritic/Metacritic) and fill the
        Critic score sort and, for games without Steam reviews, the main rating.

        IGDB wins unless its score is thin (under IGDB_MIN_CRITICS reviews) or Wikidata's
        is backed by more reviews. A Metascore still comes first for the Critic score sort.
        """
        ig_c = {"score": ig["critic"], "by": "IGDB", "n": ig.get("critic_n") or 0} if ig and ig.get("critic") else None
        wd_c = {"score": wd["score"], "by": wd["by"], "n": wd.get("n")} if wd else None
        ig_ok = bool(ig_c) and ig_c["n"] >= IGDB_MIN_CRITICS
        if ig_ok and not (wd_c and (wd_c["n"] or 0) > ig_c["n"]):
            best = ig_c
        else:
            best = wd_c
        if best:
            out["critic"], out["critic_by"], out["critic_n"] = best["score"], best["by"], best["n"]
        if out.get("metascore"):
            out["critic_score"], out["critic_src"] = out["metascore"], "Metascore"
        elif best:
            out["critic_score"] = best["score"]
            out["critic_src"] = best["by"] + (f" · {best['n']} reviews" if best["n"] else "")
        if out.get("rating") is None and out.get("percent") is None:   # no Steam reviews
            if best:
                out["rating"], out["rating_src"] = float(best["score"]), "critic"
            elif ig and ig.get("user") and (ig.get("user_n") or 0) >= IGDB_MIN_USERS:
                out["rating"], out["rating_src"] = float(ig["user"]), "igdb-user"

    def rematch_unmatched(self) -> int:
        """Look for every game without a Steam page again now (Steam search, Wikidata, IGDB),
        instead of waiting for the monthly retry. Editors and hand-set matches are left alone."""
        ov = self.overrides()
        n = 0
        with self.lock:
            matches, imap = self.cache["matches"], self.cache.get("igdb_map", {})
            for g in self.games:
                m = matches.get(g["id"])
                if g["id"] in ov or EXTRA_RE.search(g["title"]) or (m and m.get("appid")):
                    continue
                if m:
                    m["t"] = 0
                    m["wd_t"] = 0
                imap.pop(g["id"], None)
                n += 1
            self.version += 1
        if n:
            log("re-matching", n, "unmatched games")
            self.wake.set()
        return n

    def match_candidates(self, query: str, title: str = "") -> list[dict]:
        """Steam pages a user can pick for a game: Steam's store search, IGDB's games that list
        a Steam page (finds delisted games and other names), or a typed Steam app ID.
        Name results are ranked by how well they match the game's title and the search."""
        query = query.strip()
        out, seen = [], set()

        def add(appid, name, src, year=None):
            if appid and appid not in seen:
                seen.add(appid)
                out.append({"appid": int(appid), "name": name, "src": src, "year": year})
        if query.isdigit():                       # a Steam app ID
            try:
                for it in self.steam.items([int(query)]):
                    if it.get("success") == 1 or it.get("name"):
                        rel = (it.get("release") or {}).get("steam_release_date")
                        add(int(query), it.get("name") or f"app {query}", "Steam app ID", time.gmtime(int(rel)).tm_year if rel else None)
            except Exception as e:
                log("app ID lookup failed:", query, repr(e))
            return out
        if not query:
            return out
        try:
            for it in self.steam.search(query)[:8]:
                add(it.get("id"), it.get("name", ""), "Steam")
        except Exception as e:
            log("Steam search failed:", repr(e))
        creds = twitch_credentials()
        if creds:
            try:
                igdb = IGDB(*creds)
                safe = query.replace('"', " ")
                rows = igdb.query("games", f'search "{safe}"; fields id,name,first_release_date; limit 15;')
                steam = igdb.igdb_to_steam([r["id"] for r in rows])
                for r in rows:
                    rel = r.get("first_release_date")
                    add(steam.get(r["id"]), r.get("name", ""), "IGDB", time.gmtime(rel).tm_year if rel else None)
            except Exception as e:
                log("IGDB search failed:", repr(e))
        wanted = [q for q in [title, query, *search_queries(title or query)] if q]
        out.sort(key=lambda x: -max(similarity(w, x["name"]) for w in wanted))   # stable: ties keep source order
        return out[:15]

    def videos_for(self, gid: str) -> list[dict]:
        """Videos to show for one game: Steam's store videos (main trailer first), then
        IGDB's YouTube "gameplay" videos, then (only if Steam has none) IGDB's other videos."""
        now = time.time()
        g = next((x for x in self.games if x["id"] == gid), None)
        if not g:
            return []
        m = self.cache["matches"].get(gid) or {}
        out = []
        appid = m.get("appid")
        if appid:
            vc = self.cache.setdefault("videos", {})
            e = vc.get(str(appid))
            if not e or now - e.get("t", 0) > IGDB_REFRESH_SECS:
                try:
                    j = self.steam.get("https://store.steampowered.com/api/appdetails",
                                       {"appids": appid, "filters": "movies"})
                    movies = ((j.get(str(appid)) or {}).get("data") or {}).get("movies") or []
                    e = {"t": now, "movies": [{"name": mv.get("name", "Trailer"), "thumb": mv.get("thumbnail"),
                                               "hls": mv.get("hls_h264"), "highlight": bool(mv.get("highlight"))}
                                              for mv in movies if mv.get("hls_h264")]}
                    vc[str(appid)] = e
                    self.save()
                except Exception as ex:
                    log("Steam videos failed:", appid, repr(ex))
                    e = e or {"movies": []}
            for mv in sorted(e.get("movies", []), key=lambda mv: not mv["highlight"]):
                out.append({"src": "steam", "name": mv["name"], "thumb": mv["thumb"], "url": mv["hls"]})
        gid_igdb = (self.cache.get("igdb_map", {}).get(gid) or {}).get("id")
        if gid_igdb and twitch_credentials():
            det = self.cache.setdefault("igdb", {}).setdefault(str(gid_igdb), {"id": gid_igdb})
            if "videos" not in det:   # details saved before videos were fetched
                try:
                    det["videos"] = IGDB(*twitch_credentials()).videos(gid_igdb)
                    self.save()
                except Exception as ex:
                    log("IGDB videos failed:", gid_igdb, repr(ex))
            yt = det.get("videos") or []
            gameplay = [v for v in yt if re.search(r"gameplay|walkthrough|let'?s play", v["name"], re.I)]
            others = [v for v in yt if v not in gameplay] if not out else []
            for v in gameplay + others:
                out.append({"src": "youtube", "name": v["name"], "id": v["id"],
                            "thumb": f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg"})
        return out[:10]

    def _build_igdb_index(self):
        """Which library games each IGDB ID and series belongs to (for "similar"/"same series")."""
        imap, det = self.cache.get("igdb_map", {}), self.cache.get("igdb", {})
        by_id, by_series = {}, {}
        for g in self.games:
            i = (imap.get(g["id"]) or {}).get("id")
            if not i:
                continue
            by_id.setdefault(i, []).append(g["id"])
            for s_ in (det.get(str(i)) or {}).get("series") or []:
                by_series.setdefault(s_, []).append(g["id"])
        self._igdb_index = {"by_id": by_id, "by_series": by_series}

    def snapshot(self) -> dict:
        self._build_igdb_index()
        with self.lock:
            return {
                "version": self.version,
                "progress": dict(self.progress),
                "heroic_dir": str(self.heroic) if self.heroic else None,
                "steam_deck": STEAM_DECK,
                "desktop_layout": DESKTOP_LAYOUT,
                "lengths": bool(twitch_credentials()),   # IGDB data (lengths, play styles…) only with a Twitch key
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


# ------------------------------------------------------------------- Steam shortcuts
#
# In Game Mode, Steam treats everything started under our shortcut as one game, so a
# game launched through a Heroic we started would run inside our app and die with it.
# Games are therefore launched only through their own Steam shortcuts (Heroic adds one
# per installed game). Steam reads shortcuts.vdf only when it starts, so a game
# installed since then needs a Steam restart before it can be launched.
#
# Heroic itself (for install options, installs, uninstalls and progress) runs hidden
# as part of this app, with Chromium's debug port, which the app uses to drive it.

STEAM_USERDATA = ([Path(os.environ["HEROIC_RATINGS_STEAM_USERDATA"])] if os.environ.get("HEROIC_RATINGS_STEAM_USERDATA")
                  else [HOME / ".local/share/Steam/userdata", HOME / ".steam/steam/userdata"])
# --no-gui: Heroic's window stays hidden (it still loads, so the debug port works).
HEROIC_DEBUG_ARGS = ["--no-gui", "--no-sandbox", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1"]
SHORTCUT_STATE_FILE = DATA_DIR / "steam-shortcut.json"
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
    for root in STEAM_USERDATA:
        for cfg in sorted(root.glob("*/config")):
            f = cfg / "shortcuts.vdf"
            if cfg.resolve() not in seen:
                seen.add(cfg.resolve())
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


def heroic_debug_command() -> list[str]:
    """Start Heroic hidden, with its debug port (it runs as part of this app)."""
    if shutil.which("flatpak") and (HOME / ".var/app" / HEROIC_FLATPAK_ID).is_dir():
        return ["flatpak", "run", HEROIC_FLATPAK_ID, *HEROIC_DEBUG_ARGS]
    if shutil.which("heroic"):
        return ["heroic", *HEROIC_DEBUG_ARGS]
    raise OSError("Heroic isn't installed (neither the Flatpak nor a 'heroic' command was found)")


STEAM_PID_FILE = Path(os.environ.get("HEROIC_RATINGS_STEAM_PID", HOME / ".steam/steam.pid"))


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
        pid = int(STEAM_PID_FILE.read_text().strip())
        if Path(f"/proc/{pid}").exists():
            return _process_start(pid) or STEAM_PID_FILE.stat().st_mtime
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
        return json.loads(SHORTCUT_STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


_HEROIC_URL_RE = re.compile(r"heroic://launch(?:/(?P<r1>[\w-]+)/(?P<a1>[^\s\"'&?]+)|\?(?P<q>[^\s\"']+))")


def _shortcut_game(entry: dict) -> tuple[str, str] | None:
    """(runner, app_name) a Heroic-made game shortcut launches, from its heroic:// URL."""
    m = _HEROIC_URL_RE.search((_field(entry, "Exe") or "") + " " + (_field(entry, "LaunchOptions") or ""))
    if not m:
        return None
    if m.group("q"):
        q = urllib.parse.parse_qs(m.group("q"))
        app, runner = (q.get("appName") or [None])[0], (q.get("runner") or [None])[0]
        return (runner, app) if app and runner else None
    return m.group("r1"), urllib.parse.unquote(m.group("a1"))


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
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SHORTCUT_STATE_FILE.write_text(json.dumps(st))
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
    spawn_detached(["steam", url] if shutil.which("steam") else ["xdg-open", url])


# ------------------------------------------------------------------- installing via Heroic's window
#
# Heroic 2.22.4 can't start an install from outside: its heroic://launch prompt
# says "install?", but the Yes button is broken (Heroic issue #6053). Instead we
# start Heroic with Chromium's debug port (bound to 127.0.0.1), open the game's
# page in Heroic's own window and press its Install button, which shows Heroic's
# normal install dialog. Heroic does all the work; we only navigate its UI.


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
    """Port Heroic's Chromium chose (the shortcut passes port 0), from DevToolsActivePort
    in its profile folder, which is Heroic's config folder."""
    if not heroic_dir:
        return None
    f = heroic_dir / "DevToolsActivePort"
    try:
        if f.stat().st_mtime < newer_than:
            return None
        return int(f.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


# Heroic's debug port and window exist before its page has loaded (it starts on a blank
# page and then navigates to its UI), and a script run in between is destroyed by the
# navigation. So wait until the page is complete and Heroic's API is there.
_READY_JS = "document.readyState === 'complete' && !!window.api && location.protocol !== 'about:'"


def _ready_page(port: int, deadline: float) -> str | None:
    """The debug socket of Heroic's window once its UI has loaded, or None by the deadline."""
    while True:
        ws = _heroic_page_ws(port)
        if ws:
            try:
                if _cdp_eval(ws, _READY_JS, timeout=5) is True:
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
    spawn_detached(heroic_debug_command())
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


def game_shortcut_status(runner: str, app_name: str) -> str:
    """"ready" (Steam can launch it), "restart" (added since Steam started),
    "missing" (no Steam shortcut) or "steam-not-running"."""
    found = find_game_shortcut(runner, app_name)
    if not found:
        return "missing"
    if not steam_start_time():
        return "steam-not-running"
    return "ready" if shortcut_loaded_by_steam(*found) else "restart"


def launch_game(runner: str, app_name: str, heroic_dir: Path | None) -> dict:
    """Launch a game through its own Steam shortcut, so it runs as its own Steam app and
    not inside ours. Refuses (with the reason) when Steam can't launch it yet."""
    status = game_shortcut_status(runner, app_name)
    if status != "ready":
        return {"error": {"missing": "no-shortcut", "restart": "restart-steam"}.get(status, status)}
    stopped = stop_our_heroic(heroic_dir)
    if "error" in stopped:
        return stopped
    steam_run(shortcut_gameid(find_game_shortcut(runner, app_name)[0]))
    return {"via": "steam-shortcut"}


def shortcut_gameid(entry: dict) -> int:
    """64-bit game ID that steam://rungameid/ uses for a non-Steam shortcut."""
    return ((_field(entry, "appid") or ("int", 0))[1] << 32) | 0x02000000


# Heroic's window exists before Heroic has loaded its library and store logins; until
# then getGameInfo / getInstallInfo come back empty. Ask again until they answer.
_JS_UNTIL = r"""
const until = async (fn, secs) => {
  for (let i = 0; i < secs * 2; i++) {
    try { const v = await fn(); if (v) return v; } catch (e) {}
    await new Promise(r => setTimeout(r, 500));
  }
  return null;
};
for (let i = 0; i < 60 && !window.api; i++) await new Promise(r => setTimeout(r, 500));
// Heroic's own per-game prefix: <defaultWinePrefixDir>/<title without special characters>
// (same character list as Heroic's removeSpecialcharacters)
const HEROIC_SPECIAL = new RegExp("[:|/|*|?|<|>|\\\\|&|{|}|%|$|@|`|!|™|+|'|\"|®]", "gi");
const ownPrefix = (app, gs, title) => {
  if (gs && gs.winePrefix && gs.winePrefix !== app.winePrefix) return gs.winePrefix;   // already has its own
  return app.defaultWinePrefixDir + "/" + title.replaceAll(HEROIC_SPECIAL, "");
};
"""

# Runs inside Heroic's window: everything Heroic's own install dialog would offer.
# Values and defaults follow Heroic 2.22.4's DownloadDialog / WineSelector.
_INSTALL_OPTIONS_JS = r"""
(async (appName, runner, platform) => {
  %s
  const api = window.api;
  if (!api) return { error: 'not-ready' };
  const gi = await until(() => api.getGameInfo(appName, runner), 30);
  if (!gi) return { error: 'unknown-game' };
  if (gi.is_installed) return { error: 'installed' };
  const platforms = ['Windows'];
  if (gi.is_linux_native) platforms.push('linux');
  if (!platforms.includes(platform)) platform = 'Windows';
  const rp = runner === 'legendary' ? platform : (platform === 'Windows' ? 'windows' : platform);
  const info = await until(async () => { const i = await api.getInstallInfo(appName, runner, rp); return i && i.manifest ? i : null; }, 30);
  if (!info) return { error: 'no-install-info' };
  const m = info.manifest;
  const app = await api.requestAppSettings();
  const out = {
    title: gi.title, platforms, platform,
    download: m.download_size || 0, disk: m.disk_size || 0, perLang: m.perLangSize || null,
    languages: (m.languages || []).slice().sort(),
    dlcs: (runner === 'legendary' || runner === 'gog') && info.game && info.game.owned_dlc
      ? info.game.owned_dlc.map(d => ({ id: d.app_name, title: d.title, perLang: d.perLangSize || null })) : [],
    sdl: [], defaultPath: app.defaultInstallPath, wine: [], wineDefault: null,
    uiLangs: navigator.languages || [navigator.language],
  };
  if (runner === 'legendary') {
    try {
      const o = await api.getGameOverride();
      if (o && o.sdl_config && o.sdl_config[appName]) out.sdl = (await api.getGameSdl(appName)) || [];
    } catch (e) {}
  }
  if (platform === 'Windows') {
    out.wine = ((await api.getAlternativeWine()) || []).map(w => ({ name: w.name, type: w.type, bin: w.bin }));
    const gs = await api.requestGameSettings(appName);
    const cur = gs && gs.wineVersion && out.wine.find(w => w.bin === gs.wineVersion.bin);
    out.wineDefault = cur ? cur.bin : (out.wine[0] ? out.wine[0].bin : null);
    out.prefixOwn = ownPrefix(app, gs, gi.title || appName);
    out.prefixShared = app.winePrefix;
  }
  return out;
})(%s, %s, %s)
"""

# Runs inside Heroic's window: save the chosen Wine/Proton the way the dialog does,
# then queue the install with Heroic's own install function.
_INSTALL_WITH_JS = r"""
(async (p, wineBin, sharedPrefix) => {
  %s
  const api = window.api;
  if (!api) return 'not-ready';
  const gameInfo = await until(() => api.getGameInfo(p.appName, p.runner), 30);
  if (!gameInfo) return 'unknown-game';
  if (gameInfo.is_installed) return 'installed';
  const q = await api.getDMQueueInformation();   // already queued (e.g. a retried request)
  if ((q.elements || []).some(e => e.params.appName === p.appName)) return 'queued';
  if (p.platformToInstall === 'Windows') {
    // Like Heroic's install dialog: its own prefix unless "shared" was chosen, plus the Wine/Proton picked.
    const app = await api.requestAppSettings();
    const gs = await api.requestGameSettings(p.appName);
    const config = { ...gs, winePrefix: sharedPrefix ? app.winePrefix : ownPrefix(app, gs, gameInfo.title || p.appName) };
    const w = wineBin && ((await api.getAlternativeWine()) || []).find(x => x.bin === wineBin);
    if (w) config.wineVersion = w;
    await api.writeConfig({ appName: p.appName, config });
  }
  const queued = api.install({ ...p, gameInfo });
  await Promise.race([queued, new Promise(r => setTimeout(r, 5000))]);
  return 'queued';
})(%s, %s, %s)
"""

# Runs inside Heroic's window: what Heroic's uninstall dialog shows (2.22.4 UninstallModal).
_UNINSTALL_OPTIONS_JS = r"""
(async (appName, runner) => {
  %s
  const api = window.api;
  if (!api) return { error: 'not-ready' };
  const gi = await until(() => api.getGameInfo(appName, runner), 30);
  if (!gi) return { error: 'unknown-game' };
  if (!gi.is_installed) return { error: 'not-installed' };
  const native = !!(await api.isNative({ appName, runner }));
  const out = { title: gi.title, installPath: (gi.install && gi.install.install_path) || '', native,
                prefix: '', prefixShared: false, epicBusy: false };
  if (!native && gi.install && String(gi.install.platform || '').toLowerCase() === 'windows') {
    const gs = await api.requestGameSettings(appName);
    const def = await api.requestGameSettings('default');
    out.prefix = (gs && gs.winePrefix) || '';
    out.prefixShared = !!def && out.prefix === def.winePrefix;
  }
  if (runner === 'legendary') {   // Heroic can't uninstall an Epic game while another one installs
    const q = await api.getDMQueueInformation();
    out.epicBusy = q.state === 'running' && (q.elements || []).some(e => e.params.runner === 'legendary');
  }
  return out;
})(%s, %s)
"""

# Runs inside Heroic's window: start the uninstall. Heroic's call only returns when it's
# finished, so don't wait for it; progress comes through the status events.
_UNINSTALL_JS = r"""
(async (appName, runner, removePrefix, removeSettings) => {
  const api = window.api;
  if (!api) return 'not-ready';
  const p = (window.__heroicRatingsProgress || {})[appName];
  if (p && p.status === 'uninstalling') return 'started';   // already running (e.g. a retried request)
  const gi = await api.getGameInfo(appName, runner);
  if (gi && !gi.is_installed) return 'done';
  const done = api.uninstall(appName, runner, removePrefix, removeSettings).then(() => 'done', () => 'error');
  return await Promise.race([done, new Promise(r => setTimeout(() => r('started'), 3000))]);
})(%s, %s, %s, %s)
"""

# Runs inside Heroic's window: Heroic only sends progress as events, so keep the
# latest one per game in a small listener, and read the download queue.
_DOWNLOADS_JS = r"""
(async () => {
  const api = window.api;
  if (!window.__heroicRatingsProgress) {
    window.__heroicRatingsProgress = {};
    const keep = (e, st) => {
      if (st && st.appName) window.__heroicRatingsProgress[st.appName] =
        Object.assign({}, window.__heroicRatingsProgress[st.appName], st, { t: Date.now() });
    };
    api.onProgressUpdate(keep);
    api.handleGameStatus(keep);
  }
  const q = await api.getDMQueueInformation();
  const now = Date.now();
  return {
    state: q.state,
    queue: (q.elements || []).map(e => ({ appName: e.params.appName, runner: e.params.runner, type: e.type })),
    progress: window.__heroicRatingsProgress,
    uninstalling: Object.values(window.__heroicRatingsProgress)
      .filter(p => p.status === 'uninstalling' && now - p.t < 30 * 60 * 1000)
      .map(p => ({ appName: p.appName, runner: p.runner })),
  };
})()
"""


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
    r = heroic_eval(heroic_dir, _INSTALL_OPTIONS_JS % (_JS_UNTIL, json.dumps(app_name), json.dumps(runner),
                                                       json.dumps(platform or "Windows")), 100, "install options")
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
    params = {"appName": app_name, "runner": runner, "path": path,
              "platformToInstall": o.get("platform") if o.get("platform") in ("Windows", "linux") else "Windows",
              "installDlcs": strs(o.get("dlcs")), "sdlList": strs(o.get("sdl")),
              "installLanguage": o.get("language") if isinstance(o.get("language"), str) else ""}
    wine = o.get("wine") if isinstance(o.get("wine"), str) and params["platformToInstall"] == "Windows" else None
    r = heroic_eval(heroic_dir, _INSTALL_WITH_JS % (_JS_UNTIL, json.dumps(params), json.dumps(wine),
                                                    json.dumps(o.get("sharedPrefix") is True)), 60, "install")
    if "error" in r:
        return r
    log("install:", app_name, params["platformToInstall"], path, "->", r["value"])
    return {"result": r["value"]}


def uninstall_options(runner: str, app_name: str, heroic_dir: Path | None) -> dict:
    r = heroic_eval(heroic_dir, _UNINSTALL_OPTIONS_JS % (_JS_UNTIL, json.dumps(app_name), json.dumps(runner)),
                    60, "uninstall options")
    if "error" in r:
        return r
    o = r["value"]
    return o if isinstance(o, dict) else {"error": "script", "detail": repr(o)}


def start_uninstall(runner: str, app_name: str, remove_prefix: bool, remove_settings: bool,
                    heroic_dir: Path | None) -> dict:
    r = heroic_eval(heroic_dir, _UNINSTALL_JS % (json.dumps(app_name), json.dumps(runner),
                                                 json.dumps(bool(remove_prefix)), json.dumps(bool(remove_settings))),
                    15, "uninstall")
    if "error" in r:
        return r
    log("uninstall:", app_name, "prefix" if remove_prefix else "", "settings" if remove_settings else "", "->", r["value"])
    return {"result": r["value"]}


def heroic_downloads(heroic_dir: Path | None) -> dict:
    """Heroic's download queue and latest progress, if Heroic (Debug) is running. Never starts Heroic."""
    port = _active_debug_port(heroic_dir)
    ws = _heroic_page_ws(port) if port else None
    if not ws:
        return {"running": False}
    try:
        d = _cdp_eval(ws, _DOWNLOADS_JS, timeout=5)
    except (OSError, RuntimeError, ValueError):
        return {"running": False}
    return {"running": True, **(d if isinstance(d, dict) else {})}


# ------------------------------------------------------------------- install / uninstall queue
#
# Installs and uninstalls queue up here and run one at a time: each starts only when the
# previous one has finished (an install when its download has left Heroic's queue, an
# uninstall when Heroic stops reporting it). A failed job doesn't hold up the rest.

class OpQueue:
    def __init__(self, run, status, is_installed, poll_secs: float = 3, sleep=time.sleep, clock=time.time):
        """run(job) -> {"result"} | {"error"}; status() -> heroic_downloads(); is_installed(gid) -> bool."""
        self.run, self.status, self.is_installed = run, status, is_installed
        self.poll_secs, self.sleep, self.clock = poll_secs, sleep, clock
        self.jobs: list[dict] = []          # queued + running, in order
        self.finished: list[dict] = []      # most recent last (kept briefly for the UI)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.seq = 0

    # -- used by the HTTP handler
    def add(self, kind: str, game: dict, params: dict) -> dict:
        with self.lock:
            if any(j["gid"] == game["id"] for j in self.jobs):
                return {"error": "already-queued"}
            self.seq += 1
            job = {"n": self.seq, "kind": kind, "gid": game["id"], "title": game["title"], "runner": game["runner"],
                   "app_name": game["app_name"], "params": params, "state": "queued", "added": self.clock()}
            self.jobs.append(job)
            pos = len(self.jobs)
        log("queued", kind, game["title"], f"(position {pos})")
        self.wake.set()
        return {"ok": True, "position": pos}

    def cancel(self, gid: str) -> bool:
        with self.lock:
            for j in self.jobs:
                if j["gid"] == gid and j["state"] == "queued":
                    self.jobs.remove(j)
                    log("cancelled queued", j["kind"], j["title"])
                    return True
        return False

    def pending(self) -> int:
        with self.lock:
            return len(self.jobs)

    def view(self) -> list[dict]:
        """For the UI: queued/running jobs with their position, plus recent results."""
        with self.lock:
            now = self.clock()
            self.finished = [j for j in self.finished if now - j["ended"] < 120]
            return ([{"id": j["gid"], "kind": j["kind"], "state": j["state"], "position": i + 1}
                     for i, j in enumerate(self.jobs)] +
                    [{"id": j["gid"], "kind": j["kind"], "state": j["state"], "error": j.get("error"), "n": j["n"]}
                     for j in self.finished])

    # -- the worker
    def step(self) -> bool:
        """Run the next job to completion. Returns False if there was nothing to do."""
        with self.lock:
            job = self.jobs[0] if self.jobs else None
            if job:
                job["state"] = "running"
        if not job:
            return False
        log("starting", job["kind"], job["title"])
        try:
            error = self._run(job)
        except Exception as e:   # one bad job mustn't stop the queue
            log_error("queue job crashed:", job["kind"], job["title"], exc=e)
            error = "crashed"
        with self.lock:
            self.jobs.remove(job)
            job.update(state="failed" if error else "done", error=error, ended=self.clock())
            self.finished.append(job)
        log(job["kind"], job["title"], "->", f"failed ({error})" if error else "done")
        return True

    def _wait(self, still_running, max_secs: float) -> bool:
        """Poll until still_running() is False. Returns False on timeout."""
        start = self.clock()
        while still_running():
            if self.clock() - start > max_secs:
                return False
            self.sleep(self.poll_secs)
        return True

    def _run(self, job) -> str | None:
        app, gid = job["app_name"], job["gid"]
        self.status()                                   # make sure Heroic's progress listener is in place
        r = self.run(job)
        if "error" in r:
            return r.get("detail") or r["error"]
        res = r.get("result")
        if job["kind"] == "install":
            if res == "installed":
                return None
            if res != "queued":
                return f"Heroic answered {res!r}"
            seen = {"in_queue": False}
            started = self.clock()

            def downloading():
                d = self.status()
                if not d.get("running"):
                    raise RuntimeError("Heroic closed")
                inq = any(q.get("appName") == app for q in d.get("queue") or [])
                seen["in_queue"] |= inq
                # not in Heroic's queue: finished, or (if never seen) it never got there
                return inq or (not seen["in_queue"] and self.clock() - started < 30)
            try:
                if not self._wait(downloading, max_secs=24 * 3600):
                    return "timed out"
            except RuntimeError as e:
                return str(e)
            return None if self.is_installed(gid) else "Heroic finished without installing it"
        # uninstall
        if res not in ("started", "done"):
            return f"Heroic answered {res!r}"

        def uninstalling():
            d = self.status()
            return d.get("running") and any(u.get("appName") == app for u in d.get("uninstalling") or [])
        if res == "started" and not self._wait(uninstalling, max_secs=3600):
            return "timed out"
        for _ in range(10):                            # Heroic's installed list can lag a moment
            if not self.is_installed(gid):
                return None
            self.sleep(1)
        return "still installed afterwards"

    def worker(self):
        while True:
            if not self.step():
                self.wake.wait()
                self.wake.clear()

    def start(self):
        threading.Thread(target=self.worker, name="queue", daemon=True).start()
        return self


# ------------------------------------------------------------------- system health log
#
# To find out what's happening when the whole Deck freezes (e.g. during big installs):
# memory, swap, unwritten disk data, the kernel's pressure figures and the biggest
# processes, every 30 s, or every 5 s while Heroic is downloading / uninstalling.
# Each line is fsync'ed so it survives a hard reset.

def battery(root: Path = Path("/sys/class/power_supply")) -> dict | None:
    """{"percent": 0-100, "charging": bool} for the first battery, or None (no battery)."""
    try:
        supplies = sorted(root.iterdir())
    except OSError:
        return None
    for d in supplies:
        try:
            if (d / "type").read_text().strip() != "Battery":
                continue
            pct = int((d / "capacity").read_text().strip())
            status = (d / "status").read_text().strip() if (d / "status").exists() else ""
        except (OSError, ValueError):
            continue
        return {"percent": max(0, min(100, pct)), "charging": status in ("Charging", "Full"), "status": status}
    return None


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo -> {field: kB}."""
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k.strip()] = int(parts[0])
    return out


def parse_psi(text: str) -> dict[str, float]:
    """/proc/pressure/<x> -> {"some": avg10, "full": avg10} (percent of time stalled, last 10 s)."""
    out = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        for f in fields:
            if f.startswith("avg10="):
                out[kind] = float(f[6:])
    return out


def top_processes(n: int = 6, proc: Path = Path("/proc")) -> list[list]:
    """[[rss MB, name, pid]] of the n processes using the most memory."""
    rows = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            text = (d / "status").read_text()
        except OSError:
            continue
        st = parse_meminfo(text)
        name = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("Name:")), "?")
        if st.get("VmRSS"):
            rows.append([round(st["VmRSS"] / 1024), name, int(d.name)])
    return sorted(rows, reverse=True)[:n]


def health_sample() -> dict:
    def rd(path):
        try:
            return Path(path).read_text()
        except OSError:
            return ""
    m = parse_meminfo(rd("/proc/meminfo"))
    mb = lambda k: round(m.get(k, 0) / 1024)
    out = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "mem_avail_mb": mb("MemAvailable"), "mem_total_mb": mb("MemTotal"),
           "swap_used_mb": mb("SwapTotal") - mb("SwapFree"), "dirty_mb": mb("Dirty"), "writeback_mb": mb("Writeback")}
    for kind in ("memory", "io", "cpu"):
        out[f"psi_{kind}"] = parse_psi(rd(f"/proc/pressure/{kind}"))
    try:
        out["load1"] = float(rd("/proc/loadavg").split()[0])
    except (IndexError, ValueError):
        pass
    try:
        out["top"] = top_processes()
    except OSError:
        pass
    return out


class HealthMonitor:
    def __init__(self, path: Path, busy=lambda: False, idle_secs: float = 30, busy_secs: float = 5,
                 max_bytes: int = 2_000_000):
        self.path, self.busy, self.idle_secs, self.busy_secs, self.max_bytes = path, busy, idle_secs, busy_secs, max_bytes
        self.stop = threading.Event()

    def write(self, sample: dict):
        """Append one JSON line and fsync it (a hard reset must not lose it); keep one old file."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample, separators=(",", ":")) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

    def run(self):
        while not self.stop.is_set():
            busy = self.busy()
            sample = health_sample()
            sample["busy"] = busy
            self.write(sample)
            self.stop.wait(self.busy_secs if busy else self.idle_secs)

    def start(self):
        threading.Thread(target=self.run, name="health", daemon=True).start()
        return self


# ------------------------------------------------------------------- HTTP server

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


QUIET_PATHS = {"/api/downloads", "/api/log", "/api/game-shortcut", "/api/videos"}


class Budget:
    """At most `n` events per `per` seconds (so a looping UI error can't flood the log)."""
    def __init__(self, n: int, per: float):
        self.n, self.per, self.times = n, per, []

    def take(self) -> bool:
        now = time.monotonic()
        self.times = [t for t in self.times if now - t < self.per]
        if len(self.times) >= self.n:
            return False
        self.times.append(now)
        return True


ui_log_budget = Budget(30, 60)


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
            try:
                self._get()
            except Exception as e:
                log_error("GET", self.path, "crashed:", repr(e), exc=e)
                self._json({"error": "internal error"}, 500)

        def do_POST(self):
            try:
                self._post()
            except Exception as e:
                log_error("POST", self.path, "crashed:", repr(e), exc=e)
                self._json({"error": "internal error"}, 500)

        def _get(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/games":
                return self._json(state.snapshot())
            if path == "/api/status":
                state.maybe_reload_library()
                with state.lock:
                    return self._json({"app": "heroic-ratings", "version": state.version,
                                       "progress": state.progress, "battery": battery()})
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (WEB_DIR / rel).resolve()
            if WEB_DIR.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, f.read_bytes(), CONTENT_TYPES.get(f.suffix, "application/octet-stream"))

        def _post(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "bad json"}, 400)
            if path not in QUIET_PATHS:   # actions, not the UI's polling
                log("POST", path, json.dumps({k: v for k, v in body.items() if k in ("id", "force", "add", "fix", "options",
                                                                                      "removePrefix", "removeSettings", "platform")})[:300])
            if path == "/api/log":   # errors from the UI
                if ui_log_budget.take():
                    lvl = logging.ERROR if body.get("level") == "error" else logging.WARNING
                    log("UI:", str(body.get("message", ""))[:500], ("\n" + str(body.get("stack", ""))[:2000]) if body.get("stack") else "", level=lvl)
                return self._json({"ok": True})
            if path in ("/api/install-options", "/api/install-start", "/api/uninstall-options", "/api/uninstall-start"):
                state.mark_busy()
            if path == "/api/steam-setup":    # once at start-up
                try:
                    removed = remove_old_debug_shortcut()
                    note_game_shortcuts()
                except (OSError, ValueError) as e:
                    return self._json({"ok": False, "error": str(e)}, 500)
                return self._json({"ok": True, "removed_old_shortcut": bool(removed)})
            if path == "/api/game-shortcut":
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                return self._json({"ok": True, "status": game_shortcut_status(g["runner"], g["app_name"])})
            if path in ("/api/install-start", "/api/uninstall-start"):   # into the queue
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                if path == "/api/install-start":
                    o = body.get("options") or {}
                    if not isinstance(o, dict) or not isinstance(o.get("path"), str) or not o["path"].startswith("/"):
                        return self._json({"ok": False, "error": "bad-path"})
                    r = state.ops.add("install", g, o)
                else:
                    r = state.ops.add("uninstall", g, {"removePrefix": body.get("removePrefix") is True,
                                                       "removeSettings": body.get("removeSettings") is True})
                state.mark_busy()
                return self._json({"ok": "error" not in r, "result": "queued", **r})
            if path == "/api/queue-cancel":
                return self._json({"ok": state.ops.cancel(str(body.get("id", "")))})
            if path == "/api/install-options":
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                try:
                    r = install_options(g["runner"], g["app_name"], body.get("platform"), state.heroic)
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                return self._json({"ok": "error" not in r, **r})
            if path == "/api/uninstall-options":
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                try:
                    r = uninstall_options(g["runner"], g["app_name"], state.heroic)
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                return self._json({"ok": "error" not in r, **r})
            if path == "/api/reload-library":   # Heroic just finished an install/uninstall
                state.maybe_reload_library(force=True)
                return self._json({"ok": True})
            if path == "/api/settings":
                return self._json({"ok": True, "twitch": twitch_settings()})
            if path == "/api/settings/twitch":
                r = remove_twitch_credentials() if body.get("remove") else \
                    save_twitch_credentials(str(body.get("client_id", "")), str(body.get("client_secret", "")))
                if r["ok"]:
                    with state.lock:
                        state.version += 1
                    state.wake.set()   # sync now: fetch (or stop showing) IGDB data
                return self._json(r)
            if path == "/api/rematch-unmatched":
                return self._json({"ok": True, "count": state.rematch_unmatched()})
            if path == "/api/match-search":
                g = next((g for g in state.games if g["id"] == body.get("id")), None)
                return self._json({"ok": True, "results": state.match_candidates(str(body.get("query", ""))[:100],
                                                                                  g["title"] if g else "")})
            if path == "/api/videos":
                return self._json({"ok": True, "videos": state.videos_for(body.get("id", ""))})
            if path == "/api/downloads":
                d = heroic_downloads(state.heroic)
                d["jobs"] = state.ops.view()
                if d.get("queue") or d.get("uninstalling") or state.ops.pending():
                    state.mark_busy()
                return self._json(d)
            if path in ("/api/launch", "/api/install"):
                gid = body.get("id", "")
                g = next((g for g in state.games if g["id"] == gid), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                if os.environ.get("HEROIC_LAUNCH_CMD"):   # custom command: hand it the URL as-is
                    try:
                        spawn_detached(heroic_command(g["runner"], g["app_name"],
                                                      show_gui=path == "/api/install"))
                    except OSError as e:
                        return self._json({"error": str(e)}, 500)
                    return self._json({"ok": True})
                if path == "/api/install":   # installs go through /api/install-options + /api/install-start
                    return self._json({"error": "use /api/install-options"}, 400)
                if state.ops.pending():   # launching closes our Heroic, which would stop the queue
                    return self._json({"ok": False, "error": "heroic-busy"})
                try:
                    r = launch_game(g["runner"], g["app_name"], state.heroic)
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                log(path, g["title"], "->", r)
                return self._json({"ok": "error" not in r, **r})
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
        logfile = setup_logging()
        log(f"starting (data {DATA_DIR}, log {logfile}, Steam Deck {STEAM_DECK}, "
            f"Game Mode {os.environ.get('XDG_CURRENT_DESKTOP', '').lower() == 'gamescope'}, "
            f"via Steam {bool(os.environ.get('SteamGameId'))}, IGDB key {bool(twitch_credentials())})")
        state = State(args.heroic_dir)
        HealthMonitor(LOG_DIR / "health.log", busy=lambda: time.time() < state.busy_until).start()
        state.ops.start()
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
