"""The first-run sync: exact matches by store ID, Steam's app list (with a key), parallel
searching with ratings shown as they come, the Wikidata fallback in bulk, Metascores
filled in last. No network: the web services are faked."""
import json
import os
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import Sandbox, FakeWikidata, synced_cache, cfg, steam_api, igdb_api, app_state  # noqa: F401
from hr import steam_apps  # noqa: E402

EPIC = "https://www.epicgames.com/store/product/"
LIB = [
    {"app_name": "alpha", "title": "Alpha Quest", "store_url": EPIC + "alpha-quest"},
    {"app_name": "bravo", "title": "Bravo Racer", "store_url": EPIC + "bravo-racer"},
    {"app_name": "dstr", "title": "Death Stranding", "store_url": EPIC + "death-stranding"},
    {"app_name": "gamma", "title": "Gamma Online"},
]


class Sync(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox().__enter__()
        self.addCleanup(self.sb.__exit__, None, None, None)
        (self.sb.heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": LIB}))
        self.st = self.sb.state()
        self.st.wikidata = FakeWikidata(epic={"alpha-quest": [100], "death-stranding": [1190460]})
        self.st.steam.search_names = {"alpha": (100, "Alpha Quest", 85), "bravo": (200, "Bravo Racer", None),
                                      "death": (1850570, "DEATH STRANDING DIRECTOR'S CUT", 86)}
        p = mock.patch.object(igdb_api, "twitch_credentials", lambda: None)
        p.start()
        self.addCleanup(p.stop)

    def searches(self):
        return [c[1] for c in self.st.steam.calls if c[0] == "search"]

    def test_store_ids_match_exactly_without_a_search(self):
        self.st.sync()
        m = self.st.cache["matches"]
        self.assertEqual((m["legendary:alpha"]["appid"], m["legendary:alpha"]["via"]), (100, "store-id"))
        self.assertEqual(m["legendary:dstr"]["appid"], 1190460, "the edition Epic sells, not the Director's Cut")
        s = self.searches()
        self.assertEqual(s.count("Alpha Quest"), 1, "not matched by title...")
        self.assertGreater(s.index("Alpha Quest"), s.index("Bravo Racer"), "...only searched last, for its Metascore")
        self.assertIn("Bravo Racer", s, "no store-ID match: searched by title")
        self.assertEqual(m["legendary:bravo"]["appid"], 200)

    def test_a_wrong_title_match_is_corrected_by_the_monthly_recheck(self):
        self.st.cache["matches"]["legendary:dstr"] = {"title": "Death Stranding", "appid": 1850570, "name": "DS DC",
                                                       "score": 0.9, "metascore": 86, "t": time.time()}
        self.st.cache["matches"]["legendary:alpha"] = {"title": "Alpha Quest", "appid": 555, "override": True, "t": time.time()}
        cfg.OVERRIDES_FILE.write_text(json.dumps({"legendary:alpha": 555}))
        self.st.sync()
        m = self.st.cache["matches"]
        self.assertEqual((m["legendary:dstr"]["appid"], m["legendary:dstr"]["via"]), (1190460, "store-id"))
        self.assertEqual(m["legendary:alpha"]["appid"], 555, "a match you set yourself is left alone")
        calls = len(self.st.wikidata.calls)
        self.st.sync()
        self.assertEqual(len(self.st.wikidata.calls), calls, "rechecked monthly, not every sync")

    def test_two_steam_apps_for_one_store_id_falls_back_to_search(self):
        self.st.wikidata.epic["bravo-racer"] = [200, 201]
        self.st.sync()
        self.assertIn("Bravo Racer", self.searches())

    def test_installed_games_are_searched_first(self):
        (self.sb.heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": [
            {"app_name": f"g{i}", "title": f"Game {i}"} for i in range(6)]}))
        self.sb.installed_file.write_text(json.dumps({"g4": {}, "g5": {}}))
        with mock.patch.object(app_state, "SEARCH_WORKERS", 1):
            self.st.sync()
        self.assertEqual(self.searches()[0:2], ["Game 4", "Game 5"])

    def test_ratings_appear_while_searching(self):
        (self.sb.heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": [
            {"app_name": f"a{i}", "title": f"Game {i}"} for i in range(5)]}))
        self.st.steam.search_names = {f"game {i}": (1000 + i, f"Game {i}", None) for i in range(5)}
        orig = self.st.steam.search
        self.st.steam.search = lambda term: (time.sleep(0.05), orig(term))[1]    # searches take a moment
        with mock.patch.object(app_state, "REVIEW_BLOCK", 2), mock.patch.object(app_state, "SEARCH_WORKERS", 1):
            self.st.sync()
        kinds = [c[0] for c in self.st.steam.calls if c[0] in ("search", "items")]
        first_items = kinds.index("items")
        self.assertLess(first_items, max(i for i, k in enumerate(kinds) if k == "search"),
                        "reviews were fetched before the last search")

    def test_metascores_are_filled_in_for_games_matched_without_a_search(self):
        self.st.sync()
        m = self.st.cache["matches"]["legendary:alpha"]
        self.assertEqual(m["metascore"], 85)
        self.assertIn("Alpha Quest", self.searches(), "searched once, by its Steam name, for the Metascore")
        n = len(self.searches())
        self.st.sync()
        self.assertEqual(len(self.searches()), n, "not looked up again")

    def test_searches_run_in_parallel(self):
        (self.sb.heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": [
            {"app_name": f"x{i}", "title": f"Unknown {i}"} for i in range(6)]}))
        live, peak, lock = [0], [0], threading.Lock()
        orig = self.st.steam.search

        def slow(term):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
            return orig(term)
        self.st.steam.search = slow
        self.st.sync()
        self.assertGreater(peak[0], 1)
        self.assertLessEqual(peak[0], app_state.SEARCH_WORKERS)

    def test_steam_app_list_matches_locally_with_a_key(self):
        apps = {100: "Alpha Quest", 200: "Bravo Racer", 300: "Twin", 301: "Twin"}
        (self.sb.heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": [
            {"app_name": "b", "title": "Bravo Racer™"}, {"app_name": "t", "title": "Twin"}]}))
        with mock.patch.object(steam_apps, "app_list", lambda http, now=None: apps):
            self.st.sync()
        m = self.st.cache["matches"]
        self.assertEqual((m["legendary:b"]["appid"], m["legendary:b"]["via"]), (200, "applist"))
        self.assertNotIn("Bravo Racer™", self.searches())
        self.assertIn("Twin", self.searches(), "two Steam apps with that name: the search ranks them")


class SteamClientPause(unittest.TestCase):
    def test_slow_down_pauses_every_caller(self):
        s = steam_api.Steam(min_interval=0)
        with s._lock:
            s._paused_until = time.monotonic() + 0.4
        t = time.monotonic()
        real_sleep = time.sleep
        waits = []

        def sleep(secs):        # really wait for the pause; skip the retry delays after it
            waits.append(secs)
            if len(waits) == 1:
                real_sleep(secs)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")), \
                mock.patch.object(time, "sleep", sleep):
            try:
                s.get("https://x", {}, timeout=1)
            except Exception:
                pass
        self.assertGreaterEqual(waits[0], 0.3, "waited for the pause first")
        self.assertGreater(time.monotonic() - t, 0.3)


class SteamKey(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox().__enter__()
        self.addCleanup(self.sb.__exit__, None, None, None)
        p = mock.patch.dict(os.environ, {"HEROIC_RATINGS_STEAM_KEY": ""})
        p.start()
        self.addCleanup(p.stop)

    def test_saved_only_after_steam_accepts_it_and_never_shown(self):
        key = "0123456789ABCDEF0123456789ABCDEF"
        self.assertIn("doesn't look like", steam_apps.save_key("nope")["error"])
        def refused(*a, **k):
            raise urllib.error.HTTPError("https://api", 403, "Forbidden", {}, None)
        with mock.patch.object(steam_apps, "fetch_page", refused):
            self.assertIn("didn't accept", steam_apps.save_key(key)["error"])
        self.assertIsNone(steam_apps.steam_key())
        with mock.patch.object(steam_apps, "fetch_page", lambda *a, **k: ([{"appid": 10, "name": "CS"}], None)):
            r = steam_apps.save_key(key)
            self.assertEqual((r["ok"], r["set"], r["hint"]), (True, True, "0123…CDEF"))
            self.assertEqual(steam_apps.test_key(), {"ok": True})
        self.assertEqual(oct((cfg.DATA_DIR / "steam_key.json").stat().st_mode & 0o777), "0o600")
        self.assertNotIn(key, json.dumps(steam_apps.key_settings()))
        with mock.patch.object(steam_apps, "fetch_page", side_effect=urllib.error.URLError("dns")):
            self.assertIn("Check you're online", steam_apps.test_key()["error"])
        self.assertTrue(steam_apps.remove_key()["ok"])
        self.assertEqual(steam_apps.test_key()["ok"], False)

    def test_app_list_is_paged_cached_weekly_and_keyless_is_none(self):
        self.assertIsNone(steam_apps.app_list(None))
        (cfg.DATA_DIR / "steam_key.json").write_text(json.dumps({"key": "0" * 32}))
        pages = []
        def page(http, key, last, max_results=50000):
            pages.append(last)
            return ([{"appid": 10, "name": "A"}], 10) if last == 0 else ([{"appid": 20, "name": "B"}], None)
        with mock.patch.object(steam_apps, "fetch_page", page):
            self.assertEqual(steam_apps.app_list(None, now=1000), {10: "A", 20: "B"})
            self.assertEqual(steam_apps.app_list(None, now=2000), {10: "A", 20: "B"})
            self.assertEqual(pages, [0, 10], "the second call used the saved list")
            steam_apps.app_list(None, now=1000 + 8 * 86400)
            self.assertEqual(pages, [0, 10, 0, 10], "refreshed after a week")

    def test_index_only_takes_one_clear_match(self):
        idx = steam_apps.AppIndex({1: "Control", 2: "Hitman", 3: "HITMAN", 4: "The Witcher 3: Wild Hunt"})
        self.assertEqual(idx.match("Control")[0], 1)
        self.assertIsNone(idx.match("Hitman"), "two apps with the same name")
        self.assertEqual(idx.match("The Witcher 3: Wild Hunt - Game of the Year Edition")[0], 4)
        self.assertIsNone(idx.match("Something Else"))


if __name__ == "__main__":
    unittest.main()
