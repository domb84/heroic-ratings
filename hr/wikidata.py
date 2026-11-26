"""Wikidata: Steam pages and critic scores for games Steam's search misses."""
from __future__ import annotations

import re

from . import matching
from . import steam as steam_api
from .config import MATCH_THRESHOLD


# ------------------------------------------------------------------- Wikidata fallback
# Steam's store search only finds games that are currently for sale, so delisted games
# (old Football Managers, Rocket League, ...) never match. Wikidata (free, no key) maps
# game names to Steam app IDs, delisted or not, and carries Metacritic/OpenCritic scores
# for games that aren't on Steam at all (Epic exclusives).

WD_API = "https://www.wikidata.org/w/api.php"
WD_VIDEO_GAME = "Q7889"
WD_CRITICS = {"Q150248": "Metacritic", "Q21039459": "OpenCritic"}
WD_PC = {"Q1406", "Q16338"}  # Microsoft Windows, personal computer


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


class Wikidata:
    def __init__(self):
        self.http = steam_api.Steam(min_interval=1.5, name="Wikidata")  # Wikidata rate-limits fast clients

    def _is_game(self, claims) -> bool:
        p31 = {(_wd_value(c) or {}).get("id") for c in claims.get("P31", [])}
        has_oc = any("Q21039459" in _wd_qualifier_ids(c, "P447") for c in claims.get("P444", []))
        return WD_VIDEO_GAME in p31 or "P1733" in claims or has_oc

    def lookup(self, title: str) -> dict | None:
        best = None
        for q in matching.search_queries(title)[:2]:
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
                sim = max((matching.similarity(title, n) for n in names if n), default=0.0)
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
