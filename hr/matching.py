"""Matching library titles to Steam/IGDB names, and the weighted rating."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:   # type hints only
    from . import steam as steam_api

import math
import re
import unicodedata
from difflib import SequenceMatcher

from .config import MATCH_THRESHOLD


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


def best_match(steam: steam_api.Steam, title: str):
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
# Library entries that are tools/extras rather than games: don't look them up.
EXTRA_RE = re.compile(r"(?i)\b(?:editor|archiver|test(?:ing)? branch|public test(?:ing)?|playtest|"
                      r"soundtrack|ost|tech test|dedicated server|sdk|benchmark|browser|content)\b|beta\b")


def weighted_rating(percent: float, count: int) -> float:
    """SteamDB-style rating: pulls low-review-count games toward 50%."""
    avg = percent / 100.0
    return 100.0 * (avg - (avg - 0.5) * 2 ** (-math.log10(count + 1)))
