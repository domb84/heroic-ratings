"""Tests for the web clients (Steam, Wikidata, IGDB matching), install locations and
closing the app's hidden Heroic. No network: responses are faked."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from helpers import Sandbox, Server, synced_cache, cfg, logs, util, library, matching, steam_api, wikidata, igdb_api, heroic_ctl, ops, health, app_state, server  # noqa: F401
from hr import updates  # noqa: E402  (after helpers puts the app on the path)


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
        with mock.patch.object(urllib.request, "urlopen", urlopen), mock.patch.object(time, "sleep", sleeps.append):
            return steam_api.Steam(min_interval=0).get("https://store.example/api", {"term": "x"}), sleeps

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
        wd = wikidata.Wikidata()
        def get(url, params, timeout=20):
            if params["action"] == "wbsearchentities":
                return {"search": [{"id": "Q1", "label": "Football Manager 2024"}]}
            return {"entities": {"Q1": self.ENTITY}}
        wd.http.get = get
        r = wd.lookup("Football Manager 2024")
        self.assertEqual((r["qid"], r["steam"], r["metacritic"]), ("Q1", 1234, 80))
        self.assertEqual(r["critic"], {"score": 83, "by": "OpenCritic", "n": 33})

    def test_lookup_rejects_other_games(self):
        wd = wikidata.Wikidata()
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
                mock.patch.object(igdb_api, "IGDB", FakeIGDB), mock.patch.object(igdb_api, "twitch_credentials", lambda: ("id", "secret")):
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
        with Sandbox(synced_cache()) as sb, mock.patch.object(igdb_api, "twitch_credentials", lambda: None):
            st = sb.state()
            st.reload_library()
            st.sync_igdb_map(time.time(), force=False)
            st.sync_igdb_details(time.time(), force=False)
            self.assertNotIn("igdb_map", st.cache)


class TwitchTest(unittest.TestCase):
    def test_reports_ok_bad_key_and_offline(self):
        with mock.patch.object(igdb_api, "twitch_credentials", lambda: None):
            self.assertFalse(igdb_api.test_twitch_credentials()["ok"])
        with mock.patch.object(igdb_api, "twitch_credentials", lambda: ("id", "secret")):
            with mock.patch.object(igdb_api.IGDB, "query", lambda self_, e, b: [{"id": 1}]):
                self.assertEqual(igdb_api.test_twitch_credentials(), {"ok": True})
            def refused(self_, e, b):
                raise urllib.error.HTTPError("https://id.twitch.tv/oauth2/token", 400, "bad", {}, None)
            with mock.patch.object(igdb_api.IGDB, "query", refused):
                self.assertIn("Twitch didn't accept", igdb_api.test_twitch_credentials()["error"])
            with mock.patch.object(igdb_api.IGDB, "query", lambda self_, e, b: (_ for _ in ()).throw(urllib.error.URLError("dns"))):
                self.assertIn("Check you're online", igdb_api.test_twitch_credentials()["error"])


class InstallLocations(unittest.TestCase):
    def test_default_folder_and_free_space(self):
        with tempfile.TemporaryDirectory() as d:
            locs = heroic_ctl.install_locations(str(Path(d, "not", "created", "yet")))
            self.assertEqual(locs[0]["label"], "Heroic's default")
            self.assertGreater(locs[0]["free"], 0)      # measured on the nearest existing folder
            self.assertEqual(heroic_ctl.install_locations(None)[:0], [])


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
        with mock.patch.object(heroic_ctl, "heroic_downloads", lambda d: {"running": True, "queue": [{"runner": "legendary", "appName": "x"}]}):
            self.assertEqual(heroic_ctl.stop_our_heroic(None)["error"], "heroic-busy")
        self.assertIsNone(ours.poll())
        with mock.patch.object(heroic_ctl, "heroic_downloads", lambda d: {"running": True, "queue": [], "uninstalling": []}):
            self.assertEqual(heroic_ctl.stop_our_heroic(None), {})
        self.assertIsNotNone(ours.wait(5))
        self.assertIsNone(theirs.poll(), "a Heroic the user started is left alone")


class UpdateCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "updates.json")
        self.now = 1_000_000.0
        self.calls = 0
        self.answer = {"latest": "v1.1.0", "url": "https://github.com/x/releases/tag/v1.1.0"}

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def make(self, current="v1.0.0"):
        return updates.Updates(self.path, current=current, fetch=self.fetch, clock=lambda: self.now)

    def test_versions(self):
        self.assertEqual(updates.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updates.parse_version("1.2"), (1, 2, 0))
        self.assertEqual(updates.parse_version("v1.0.0-4-gabc123-dirty"), (1, 0, 0))
        for v in ("dev", "5f2360f", "", None):
            self.assertIsNone(updates.parse_version(v))

    def test_newer_release_is_reported_and_checked_once_a_day(self):
        u = self.make()
        u.check()
        self.assertEqual({k: u.available()[k] for k in ("version", "url")}, {"version": "v1.1.0", "url": self.answer["url"]})
        u.check()
        self.now += 3600
        self.make().check()                               # restarting doesn't ask again either
        self.assertEqual(self.calls, 1)
        self.now += 24 * 3600
        self.make().check()
        self.assertEqual(self.calls, 2)

    def test_same_or_older_release_and_dev_builds_say_nothing(self):
        for current in ("v1.1.0", "v1.1.0-3-gabc", "v2.0.0"):
            self.path.unlink(missing_ok=True)
            u = self.make(current)
            u.check()
            self.assertIsNone(u.available(), current)
        self.path.unlink()
        u = self.make("5f2360f-dirty")
        u.check()
        self.assertEqual((self.calls, u.available(), u.view()["checkable"]), (3, None, False), "untagged builds never ask")

    def test_offline_is_quiet_and_retried_tomorrow(self):
        self.answer = urllib.error.URLError("offline")
        u = self.make()
        u.check()
        self.assertIsNone(u.available())
        u.check()
        self.assertEqual(self.calls, 1)

    def test_turning_it_off(self):
        u = self.make()
        u.check()
        u.set_enabled(False)
        self.assertIsNone(u.available())
        self.now += 7 * 24 * 3600
        self.make().check()
        self.assertEqual(self.calls, 1, "no requests while it's off")
        u = self.make()
        u.set_enabled(True)
        u.check()
        self.assertEqual(self.calls, 2, "turning it back on checks straight away")

    def test_release_assets_are_found(self):
        rel = {"tag_name": "v1.1.0", "html_url": "https://github.com/domb84/heroic-ratings/releases/tag/v1.1.0", "assets": [
            {"name": "HeroicRatings-v1.1.0-x86_64.AppImage.sha256", "browser_download_url": "https://github.com/domb84/heroic-ratings/releases/download/v1.1.0/a.sha256"},
            {"name": "HeroicRatings-v1.1.0-x86_64.AppImage", "size": 5, "browser_download_url": "https://github.com/domb84/heroic-ratings/releases/download/v1.1.0/a"}]}
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: FakeResponse(json.dumps(rel).encode())):
            r = updates.fetch_latest()
        self.assertEqual((r["latest"], r["size"]), ("v1.1.0", 5))
        self.assertTrue(r["appimage"].endswith("/a") and r["sha256_url"].endswith("/a.sha256"))


    def test_through_the_api(self):
        with Sandbox(synced_cache()) as sb:
            st = sb.state()
            st.updates = self.make()
            st.updates.check()
            srv = Server(st)
            try:
                self.assertEqual(json.loads(srv.get("/api/status")[1])["update"]["version"], "v1.1.0")
                v = srv.post("/api/settings")[1]["updates"]
                self.assertEqual((v["enabled"], v["available"]["version"]), (True, "v1.1.0"))
                v = srv.post("/api/settings/updates", {"enabled": False})[1]["updates"]
                self.assertEqual((v["enabled"], v["available"]), (False, None))
                self.assertIsNone(json.loads(srv.get("/api/status")[1])["update"])
            finally:
                srv.close()


class SelfUpdate(unittest.TestCase):
    """Downloading the new AppImage, checking it, swapping it in."""
    BASE = "https://github.com/domb84/heroic-ratings/releases/download/v1.1.0/"
    NEW = b"\x7fELF new appimage" + b"x" * 3000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Path(self.tmp.name, "HeroicRatings-x86_64.AppImage")
        self.app.write_bytes(b"\x7fELF old")
        self.served = {self.BASE + "a": self.NEW,
                       self.BASE + "a.sha256": (__import__("hashlib").sha256(self.NEW).hexdigest() + "  a\n").encode()}

    def tearDown(self):
        self.tmp.cleanup()

    def opener(self, url, timeout=30):
        r = FakeResponse(self.served[url])
        r.headers = {"Content-Length": str(len(self.served[url]))}
        return r

    def make(self, where="default", **release):
        rel = {"latest": "v1.1.0", "url": "u", "appimage": self.BASE + "a", "sha256_url": self.BASE + "a.sha256",
               "size": len(self.NEW), **release}
        self.n = getattr(self, "n", 0) + 1     # its own saved state each time (else the daily limit applies)
        u = updates.Updates(Path(self.tmp.name, f"updates{self.n}.json"), current="v1.0.0", fetch=lambda: rel,
                            appimage=str(self.app) if where == "default" else where, opener=self.opener)
        u.check()
        return u

    def test_installs_a_checked_download_in_place(self):
        u = self.make()
        self.assertTrue(u.available()["can_install"])
        u.install()
        self.assertEqual(u.install_state, {"phase": "done", "version": "v1.1.0"})
        self.assertEqual(self.app.read_bytes(), self.NEW)
        self.assertTrue(os.access(self.app, os.X_OK))
        self.assertEqual(sorted(p.name for p in self.app.parent.iterdir()), ["HeroicRatings-x86_64.AppImage", "updates1.json"])

    def test_a_bad_download_leaves_the_app_alone(self):
        for bad in (b"\x7fELF tampered", b"<html>not found</html>"):
            self.served[self.BASE + "a"] = bad
            if not bad.startswith(b"\x7fELF"):    # right checksum, but not an AppImage
                self.served[self.BASE + "a.sha256"] = __import__("hashlib").sha256(bad).hexdigest().encode()
            u = self.make()
            u.install()
            self.assertEqual(u.install_state["phase"], "error", bad)
            self.assertEqual(self.app.read_bytes(), b"\x7fELF old")
            self.assertEqual(len(list(self.app.parent.glob(".*update"))), 0, "no partial file left behind")
        self.served[self.BASE + "a.sha256"] = b"<html>rate limited</html>"
        u = self.make()
        u.install()
        self.assertIn("checksum", u.install_state["error"])

    def test_only_from_this_repo_and_only_as_an_appimage(self):
        self.assertEqual(self.make(where=None).can_install(), "not-appimage")
        self.assertEqual(self.make(where="").can_install(), "not-appimage")
        self.assertEqual(self.make(where=str(Path(self.tmp.name, "gone.AppImage"))).can_install(), "not-writable")
        evil = self.make(**{"appimage": "https://evil.example/HeroicRatings.AppImage"})
        self.assertEqual(evil.can_install(), "no-download")
        self.assertEqual(evil.start_install(), {"ok": False, "error": "no-download"})
        nothing_newer = updates.Updates(Path(self.tmp.name, "u2.json"), current="v1.1.0", fetch=lambda: {"latest": "v1.1.0", "url": "u"},
                                        appimage=str(self.app), opener=self.opener)
        nothing_newer.check()
        self.assertEqual(nothing_newer.start_install()["error"], "no-update")

    def test_start_install_runs_in_the_background(self):
        u = self.make()
        self.assertEqual(u.start_install(), {"ok": True})
        for _ in range(100):
            if u.install_state.get("phase") == "done":
                break
            time.sleep(0.02)
        self.assertEqual(u.available()["install"]["phase"], "done")
        self.assertEqual(self.app.read_bytes(), self.NEW)


if __name__ == "__main__":
    unittest.main()
