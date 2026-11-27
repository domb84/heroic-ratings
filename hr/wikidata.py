"""Wikidata: Steam pages and critic scores for games Steam's search misses."""
from __future__ import annotations

import json
import re

from . import matching
from . import steam as steam_api
from .config import MATCH_THRESHOLD
from .logs import log


# ------------------------------------------------------------------- Wikidata fallback
# Steam's store search only finds games that are currently for sale, so delisted games
# (old Football Managers, Rocket League, ...) never match. Wikidata (free, no key) maps
# game names to Steam app IDs, delisted or not, and carries Metacritic/OpenCritic scores
# for games that aren't on Steam at all (Epic exclusives).

WD_API = "https://www.wikidata.org/w/api.php"
WD_SPARQL = "https://query.wikidata.org/sparql"
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
        self.query_http = steam_api.Steam(min_interval=1.0, name="Wikidata query")

    def steam_ids_by_epic(self, slugs: list[str]) -> dict[str, set[int]]:
        """{Epic store slug: Steam app IDs} for games Wikidata links to both (Epic Games
        Store ID, P6278; Steam app ID, P1733). Hundreds per request."""
        out: dict[str, set[int]] = {}
        slugs = sorted({s for s in slugs if re.fullmatch(r"[a-z0-9][a-z0-9-]*", s)})
        for i in range(0, len(slugs), 300):
            vals = " ".join(f'"{s}"' for s in slugs[i:i + 300])
            q = f"SELECT ?slug ?steam WHERE {{ VALUES ?slug {{ {vals} }} ?item wdt:P6278 ?slug ; wdt:P1733 ?steam . }}"
            d = self.query_http.get(WD_SPARQL, {}, timeout=60, form={"query": q, "format": "json"})
            for r in (d.get("results") or {}).get("bindings") or []:
                steam = (r.get("steam") or {}).get("value", "")
                if steam.isdigit():
                    out.setdefault(r["slug"]["value"], set()).add(int(steam))
        return out

    def _is_game(self, claims) -> bool:
        p31 = {(_wd_value(c) or {}).get("id") for c in claims.get("P31", [])}
        has_oc = any("Q21039459" in _wd_qualifier_ids(c, "P447") for c in claims.get("P444", []))
        return WD_VIDEO_GAME in p31 or "P1733" in claims or has_oc

    def lookup(self, title: str) -> dict | None:
        return self.lookup_many([title]).get(title)

    def lookup_many(self, titles: list[str], progress=None) -> dict[str, dict | None]:
        """lookup() for many titles: one search per title, then the candidates' details
        fetched 50 at a time (instead of a details request per title)."""
        best: dict[str, tuple] = {}
        pending = list(dict.fromkeys(titles))
        # First, exact names in one query (Wikidata's search API is slow to use title by
        # title: it asks fast clients to wait), then search for the titles still missing.
        try:
            by_label = self._items_by_exact_name(pending)
        except Exception as e:   # the query service is optional: search covers these
            log("Wikidata name query failed:", e)
            by_label = {}
        if by_label:
            ents = self._entities(sorted({q for hits in by_label.values() for q, _ in hits}))
            for t, hits in by_label.items():
                b = self._best(t, [{"id": q, "label": n} for q, n in hits], ents)
                if b:
                    best[t] = b
            pending = [t for t in pending if t not in best]
        log(f"Wikidata: {len(best)} of {len(titles)} found by exact name; searching for {len(pending)}")
        for attempt in range(2):                      # the title, then a shortened form
            terms = {}
            for t in pending:
                qs = matching.search_queries(t)[:2]
                if attempt < len(qs):
                    terms.setdefault(qs[attempt], []).append(t)
            if progress and terms:
                progress(len(titles) - len(pending), len(titles), f"searching for {len(pending)} titles")
            found = self._search_many(list(terms))
            hits_by_title = {t: found[term] for term, ts in terms.items() if found.get(term) for t in ts}
            ents = self._entities(sorted({h["id"] for hits in hits_by_title.values() for h in hits}))
            for t, hits in hits_by_title.items():
                b = self._best(t, hits, ents)
                if b:
                    best[t] = b
            pending = [t for t in pending if t not in best]
            if not pending:
                break
        return {t: self._result(best[t]) if t in best else None for t in titles}

    def _search_many(self, terms: list[str]) -> dict[str, list[dict]]:
        """{term: [{id, label}]}: Wikidata's search for many terms, run by its query service
        in one request per 25 terms. (Its search API, used term by term, makes fast
        clients wait 45 s at a time.) Falls back to that API if the query service fails."""
        out: dict[str, list[dict]] = {}
        for i in range(0, len(terms), 25):
            chunk = terms[i:i + 25]
            vals = " ".join(json.dumps(t) for t in chunk)
            q = (f"SELECT ?term ?item ?label WHERE {{ VALUES ?term {{ {vals} }} SERVICE wikibase:mwapi {{ "
                 f'bd:serviceParam wikibase:endpoint "www.wikidata.org"; wikibase:api "EntitySearch"; '
                 f'mwapi:search ?term; mwapi:language "en"; mwapi:limit "7". '
                 f'?item wikibase:apiOutputItem mwapi:item. ?label wikibase:apiOutput "@label". }} }}')
            try:
                d = self.query_http.get(WD_SPARQL, {}, timeout=60, form={"query": q, "format": "json"})
                for r in (d.get("results") or {}).get("bindings") or []:
                    out.setdefault(r["term"]["value"], []).append(
                        {"id": r["item"]["value"].rsplit("/", 1)[-1], "label": (r.get("label") or {}).get("value", "")})
            except Exception as e:
                log("Wikidata search query failed, searching one by one:", e)
                for term in chunk:
                    res = self.http.get(WD_API, {"action": "wbsearchentities", "search": term, "language": "en",
                                                 "type": "item", "limit": 7, "format": "json"})
                    if res.get("search"):
                        out[term] = res["search"]
        return out

    def _entities(self, ids: list[str]) -> dict:
        """Claims, labels and aliases of items, 50 per request."""
        ents: dict = {}
        for i in range(0, len(ids), 50):
            ents.update(self.http.get(WD_API, {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]),
                                               "props": "claims|labels|aliases", "languages": "en|mul",
                                               "format": "json"}).get("entities", {}))
        return ents

    def _items_by_exact_name(self, titles: list[str]) -> dict[str, list[tuple[str, str]]]:
        """{title: [(item, name)]} for items whose English (or language-neutral) label or
        alias is exactly the title or its shortened form. One query for 100 titles."""
        names: dict[str, set[str]] = {}
        for t in titles:
            for q in matching.search_queries(t)[:2]:
                names.setdefault(q, set()).add(t)
        out: dict[str, list[tuple[str, str]]] = {}
        keys = sorted(names)
        for i in range(0, len(keys), 100):
            vals = " ".join(f"{json.dumps(n)}@{lang}" for n in keys[i:i + 100] for lang in ("en", "mul"))
            q = (f"SELECT DISTINCT ?item ?name WHERE {{ VALUES ?name {{ {vals} }} "
                 f"?item rdfs:label|skos:altLabel ?name . }} LIMIT 2000")
            d = self.query_http.get(WD_SPARQL, {}, timeout=60, form={"query": q, "format": "json"})
            for r in (d.get("results") or {}).get("bindings") or []:
                qid = r["item"]["value"].rsplit("/", 1)[-1]
                name = r["name"]["value"]
                for t in names.get(name, ()):
                    if (qid, name) not in out.setdefault(t, []):
                        out[t].append((qid, name))
        return out

    def _best(self, title: str, hits: list[dict], ents: dict):
        # Names the search matched on (covers language-neutral "mul" labels too).
        found = {s["id"]: [s.get("label", ""), (s.get("match") or {}).get("text", "")] for s in hits}
        best = None
        for qid in [s["id"] for s in hits]:
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
        return best

    def _result(self, best) -> dict:
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
