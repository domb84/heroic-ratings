"""The app's data: library + cache, the sync with Steam/Wikidata/IGDB, per-game views."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from . import config as cfg
from . import heroic as heroic_ctl
from . import igdb as igdb_api
from . import library
from . import matching
from . import ops
from . import steam as steam_api
from . import updates
from . import util
from . import wikidata
from .config import APP_VERSION, DESKTOP_LAYOUT, GENRE_SET, IGDB_GENRES, IGDB_MIN_CRITICS, IGDB_MIN_USERS, IGDB_MISS_RETRY_SECS, IGDB_REFRESH_SECS, NOMATCH_RETRY_SECS, STEAM_DECK, STEAM_REFRESH_SECS, TAGLIST_REFRESH_SECS

from .logs import log, log_error
from .matching import EXTRA_RE


# ------------------------------------------------------------------- app state

class State:
    def __init__(self, heroic_dir: str | None):
        self.heroic_arg = heroic_dir
        self.lock = threading.RLock()
        self.steam = steam_api.Steam()
        self.wikidata = wikidata.Wikidata()
        self.cache = util.read_json(cfg.CACHE_FILE, {}) or {}
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
        self.ops = ops.OpQueue(run=self._run_op, status=lambda: heroic_ctl.heroic_downloads(self.heroic), is_installed=self._installed_now,
                                  path=cfg.DATA_DIR / "queue.json")
        self.updates = updates.Updates()

    def _run_op(self, job: dict) -> dict:
        p = job["params"]
        if job["kind"] == "install":
            return heroic_ctl.install_with_options(job["runner"], job["app_name"], p, self.heroic)
        return heroic_ctl.start_uninstall(job["runner"], job["app_name"], p.get("removePrefix") is True,
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
            util.write_json_atomic(cfg.CACHE_FILE, self.cache)

    def overrides(self) -> dict:
        return util.read_json(cfg.OVERRIDES_FILE, {}) or {}

    def set_override(self, gid: str, appid):
        with self.lock:
            ov = self.overrides()
            ov[gid] = appid
            util.write_json_atomic(cfg.OVERRIDES_FILE, ov)
            self.cache["matches"].pop(gid, None)
            self.version += 1
        self.wake.set()

    # -- library
    def reload_library(self):
        heroic = library.find_heroic_dir(self.heroic_arg)
        games = library.load_library(heroic) if heroic else []
        with self.lock:
            self.heroic, self.games = heroic, games
            self.version += 1
        log(f"library: {len(games)} games from {heroic}")
        self.ops.ready.set()

    def _library_signature(self):
        if not self.heroic:
            return None
        files = heroic_ctl.adapter().watched_files()
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
                appid, name, score, meta = matching.best_match(self.steam, g["title"])
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
        creds = igdb_api.twitch_credentials()
        if not creds:
            return
        igdb = igdb_api.IGDB(*creds)
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
        creds = igdb_api.twitch_credentials()
        if not creds:
            return
        imap, details = self.cache.get("igdb_map", {}), self.cache.setdefault("igdb", {})
        ids = sorted({e["id"] for e in imap.values() if e.get("id")})
        stale = [i for i in ids if force or str(i) not in details
                 or now - details[str(i)].get("t", 0) > IGDB_REFRESH_SECS]
        if not stale:
            return
        igdb = igdb_api.IGDB(*creds)
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
                "rating": round(matching.weighted_rating(pct, cnt), 1) if pct is not None and cnt else None,
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
        creds = igdb_api.twitch_credentials()
        if creds:
            try:
                igdb = igdb_api.IGDB(*creds)
                safe = query.replace('"', " ")
                rows = igdb.query("games", f'search "{safe}"; fields id,name,first_release_date; limit 15;')
                steam = igdb.igdb_to_steam([r["id"] for r in rows])
                for r in rows:
                    rel = r.get("first_release_date")
                    add(steam.get(r["id"]), r.get("name", ""), "IGDB", time.gmtime(rel).tm_year if rel else None)
            except Exception as e:
                log("IGDB search failed:", repr(e))
        wanted = [q for q in [title, query, *matching.search_queries(title or query)] if q]
        out.sort(key=lambda x: -max(matching.similarity(w, x["name"]) for w in wanted))   # stable: ties keep source order
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
        if gid_igdb and igdb_api.twitch_credentials():
            det = self.cache.setdefault("igdb", {}).setdefault(str(gid_igdb), {"id": gid_igdb})
            if "videos" not in det:   # details saved before videos were fetched
                try:
                    det["videos"] = igdb_api.IGDB(*igdb_api.twitch_credentials()).videos(gid_igdb)
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
                "app_version": APP_VERSION,
                "desktop_layout": DESKTOP_LAYOUT,
                "lengths": bool(igdb_api.twitch_credentials()),   # IGDB data (lengths, play styles…) only with a Twitch key
                "games": [self.game_view(g) for g in self.games],
            }
