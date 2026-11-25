"""Tests for the web clients (Steam, Wikidata, IGDB matching), install locations and
closing the app's hidden Heroic. No network: responses are faked."""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from helpers import Sandbox, synced_cache, h


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(code, retry_after=None):
    hdrs = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://x", code, "err", hdrs, None)


class SteamClient(unittest.TestCase):
    def run_get(self, responses):
        sleeps = []
        it = iter(responses)

        def urlopen(req, timeout=None):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return FakeResponse(json.dumps(r).encode())
        with mock.patch.object(h.urllib.request, "urlopen", urlopen), mock.patch.object(h.time, "sleep", sleeps.append):
            return h.Steam(min_interval=0).get("https://store.example/api", {"term": "x"}), sleeps

    def test_ok(self):
        r, sleeps = self.run_get([{"items": [1]}])
        self.assertEqual(r, {"items": [1]})
        self.assertEqual(sleeps, [])

    def test_backs_off_and_honours_retry_after(self):
        r, sleeps = self.run_get([http_error(429, "7"), http_error(503), {"ok": True}])
        self.assertEqual(r, {"ok": True})
        self.assertEqual(sleeps, [8, 10])   # Retry-After + 1, then 5 * 2^1

    def test_network_errors_are_retried(self):
        r, sleeps = self.run_get([urllib.error.URLError("dns"), {"ok": 1}])
        self.assertEqual((r, sleeps), ({"ok": 1}, [3]))

    def test_other_http_errors_raise(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.run_get([http_error(404)])

    def test_gives_up_eventually(self):
        with self.assertRaises(RuntimeError):
            self.run_get([urllib.error.URLError("down")] * 6)


class WikidataClient(unittest.TestCase):
    ENTITY = {"claims": {
        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q7889"}}}}],
        "P1733": [{"mainsnak": {"datavalue": {"value": "1234"}}}],
        "P444": [
            {"mainsnak": {"datavalue": {"value": "83/100"}},
             "qualifiers": {"P447": [{"datavalue": {"value": {"id": "Q21039459"}}}],
                            "P7887": [{"datavalue": {"value": {"amount": "+33"}}}]}},
            {"mainsnak": {"datavalue": {"value": "86%"}},     # "% recommended": not a score
             "qualifiers": {"P447": [{"datavalue": {"value": {"id": "Q21039459"}}}]}},
            {"mainsnak": {"datavalue": {"value": "80/100"}},
             "qualifiers": {"P447": [{"datavalue": {"value": {"id": "Q150248"}}}]}},
        ]},
        "labels": {"en": {"value": "Football Manager 2024"}}, "aliases": {}}

    def test_lookup_finds_steam_page_and_scores(self):
        wd = h.Wikidata()
        def get(url, params, timeout=20):
            if params["action"] == "wbsearchentities":
                return {"search": [{"id": "Q1", "label": "Football Manager 2024"}]}
            return {"entities": {"Q1": self.ENTITY}}
        wd.http.get = get
        r = wd.lookup("Football Manager 2024")
        self.assertEqual((r["qid"], r["steam"], r["metacritic"]), ("Q1", 1234, 80))
        self.assertEqual(r["critic"], {"score": 83, "by": "OpenCritic", "n": 33})

    def test_lookup_rejects_other_games(self):
        wd = h.Wikidata()
        wd.http.get = lambda url, params, timeout=20: (
            {"search": [{"id": "Q1", "label": "Football Manager 2024"}]} if params["action"] == "wbsearchentities"
            else {"entities": {"Q1": self.ENTITY}})
        self.assertIsNone(wd.lookup("Space Trader 3000"))


class FakeIGDB:
    """Stands in for heroic_ratings.IGDB during a sync."""
    def __init__(self, *creds):
        pass

    def steam_to_igdb(self, appids):
        return {a: a * 10 for a in appids}

    def search_name(self, title):
        return (3, "Gamma Online", 1.0) if "Gamma" in title else None

    def igdb_to_steam(self, ids):
        return {3: 300} if 3 in ids else {}

    def details(self, ids):
        return {i: {"id": i, "name": f"g{i}", "user": 75, "user_n": 10, "critic": None, "critic_n": 0, "modes": ["Single player"],
                    "persp": [], "series": [], "similar": [], "main": 5.0, "extra": None, "comp": None} for i in ids}


class IgdbSync(unittest.TestCase):
    def test_matching_links_steam_and_fetches_details(self):
        with Sandbox(synced_cache()) as sb, \
                mock.patch.object(h, "IGDB", FakeIGDB), mock.patch.object(h, "twitch_credentials", lambda: ("id", "secret")):
            st = sb.state()
            st.reload_library()
            st.sync_igdb_map(time.time(), force=False)
            imap, m = st.cache["igdb_map"], st.cache["matches"]
            self.assertEqual(imap["legendary:alpha"]["id"], 1000)            # by Steam app ID
            self.assertEqual(imap["legendary:gamma"]["id"], 3)               # by name
            self.assertEqual((m["legendary:gamma"]["appid"], m["legendary:gamma"]["via"]), (300, "igdb"))
            self.assertNotIn("legendary:ued", imap, "editors aren't looked up")
            st.sync_igdb_details(time.time(), force=False)
            self.assertEqual(st.cache["igdb"]["1000"]["main"], 5.0)
            self.assertEqual(set(st.cache["igdb"]), {"1000", "2000", "3"})

    def test_without_a_key_nothing_happens(self):
        with Sandbox(synced_cache()) as sb, mock.patch.object(h, "twitch_credentials", lambda: None):
            st = sb.state()
            st.reload_library()
            st.sync_igdb_map(time.time(), force=False)
            st.sync_igdb_details(time.time(), force=False)
            self.assertNotIn("igdb_map", st.cache)


class InstallLocations(unittest.TestCase):
    def test_default_folder_and_free_space(self):
        with tempfile.TemporaryDirectory() as d:
            locs = h.install_locations(str(Path(d, "not", "created", "yet")))
            self.assertEqual(locs[0]["label"], "Heroic's default")
            self.assertGreater(locs[0]["free"], 0)      # measured on the nearest existing folder
            self.assertEqual(h.install_locations(None)[:0], [])


class StopOurHeroic(unittest.TestCase):
    """Closing the app's hidden Heroic, with stand-in processes named like Heroic."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = Path(self.tmp.name, "heroic", "heroic")
        self.fake.parent.mkdir()
        shutil.copy(sys.executable, self.fake)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        self.tmp.cleanup()

    def start(self, debug):
        p = subprocess.Popen([str(self.fake), "-c", "import time; time.sleep(60)"] + (["--remote-debugging-port=0"] if debug else []))
        self.procs.append(p)
        time.sleep(0.4)
        return p

    def test_refuses_while_busy_closes_when_idle_never_touches_users_heroic(self):
        ours, theirs = self.start(debug=True), self.start(debug=False)
        with mock.patch.object(h, "heroic_downloads", lambda d: {"running": True, "queue": [{"runner": "legendary", "appName": "x"}]}):
            self.assertEqual(h.stop_our_heroic(None)["error"], "heroic-busy")
        self.assertIsNone(ours.poll())
        with mock.patch.object(h, "heroic_downloads", lambda d: {"running": True, "queue": [], "uninstalling": []}):
            self.assertEqual(h.stop_our_heroic(None), {})
        self.assertIsNotNone(ours.wait(5))
        self.assertIsNone(theirs.poll(), "a Heroic the user started is left alone")


if __name__ == "__main__":
    unittest.main()
