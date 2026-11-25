"""Tests for logging, the health monitor, the sync, the per-game view and the HTTP API."""
import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path

from helpers import Sandbox, Server, synced_cache, h


class Logging(unittest.TestCase):
    def tearDown(self):
        for hd in [hd for hd in h.logger.handlers if isinstance(hd, logging.FileHandler)]:
            h.logger.removeHandler(hd)
            hd.close()

    def test_app_log_written_with_tracebacks(self):
        with tempfile.TemporaryDirectory() as d:
            f = h.setup_logging(Path(d))
            h.log("hello", 42)
            try:
                raise ValueError("boom")
            except ValueError:
                h.log_error("something failed")
            text = f.read_text()
            self.assertIn("INFO", text)
            self.assertIn("hello 42", text)
            self.assertIn("ERROR", text)
            self.assertIn("Traceback", text)
            self.assertIn("ValueError: boom", text)

    def test_setup_twice_keeps_one_file_handler(self):
        with tempfile.TemporaryDirectory() as d:
            h.setup_logging(Path(d))
            h.setup_logging(Path(d))
            self.assertEqual(sum(isinstance(x, logging.FileHandler) for x in h.logger.handlers), 1)

    def test_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            h.setup_logging(Path(d), max_bytes=300, backups=2)
            for i in range(40):
                h.log("line", i, "x" * 30)
            files = sorted(p.name for p in Path(d).iterdir())
            self.assertEqual(files, ["app.log", "app.log.1", "app.log.2"])

    def test_unwritable_dir_doesnt_crash(self):
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d, "file")
            blocker.write_text("x")
            self.assertIsNone(h.setup_logging(blocker / "logs"))   # a file in the way
            h.log("still fine")


class Health(unittest.TestCase):
    MEMINFO = "MemTotal:       15150008 kB\nMemAvailable:    8000000 kB\nSwapTotal:      24000000 kB\nSwapFree:       23000000 kB\nDirty:             51200 kB\nWriteback:          1024 kB\nHugePages_Total:       0\n"

    def test_parse_meminfo(self):
        m = h.parse_meminfo(self.MEMINFO)
        self.assertEqual(m["MemTotal"], 15150008)
        self.assertEqual(m["Dirty"], 51200)
        self.assertEqual(m["HugePages_Total"], 0)

    def test_parse_psi(self):
        text = "some avg10=12.50 avg60=3.00 avg300=1.00 total=123\nfull avg10=4.25 avg60=1.00 avg300=0.10 total=45\n"
        self.assertEqual(h.parse_psi(text), {"some": 12.5, "full": 4.25})
        self.assertEqual(h.parse_psi(""), {})

    def test_top_processes(self):
        with tempfile.TemporaryDirectory() as d:
            for pid, name, rss in [(1, "small", 1024), (2, "big", 4096000), (3, "mid", 512000)]:
                Path(d, str(pid)).mkdir()
                Path(d, str(pid), "status").write_text(f"Name:\t{name}\nVmRSS:\t{rss} kB\n")
            Path(d, "4").mkdir()                     # a kernel thread: no VmRSS
            Path(d, "4", "status").write_text("Name:\tkworker\n")
            Path(d, "self").mkdir()                  # not a PID
            top = h.top_processes(2, proc=Path(d))
            self.assertEqual(top, [[4000, "big", 2], [500, "mid", 3]])

    def test_sample_has_the_fields(self):
        s = h.health_sample()
        for k in ("t", "mem_avail_mb", "mem_total_mb", "swap_used_mb", "dirty_mb", "psi_memory", "psi_io"):
            self.assertIn(k, s)
        self.assertGreater(s["mem_total_mb"], 0)

    def test_monitor_writes_lines_and_rotates(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "logs", "health.log")
            mon = h.HealthMonitor(f, max_bytes=200)
            for i in range(6):
                mon.write({"i": i, "pad": "x" * 60})
            self.assertTrue(f.with_name("health.log.1").exists())
            last = [json.loads(l) for l in f.read_text().splitlines()]
            self.assertEqual(last[-1]["i"], 5)

    def test_monitor_samples_faster_when_busy(self):
        with tempfile.TemporaryDirectory() as d:
            waits = []
            mon = h.HealthMonitor(Path(d, "health.log"), busy=lambda: len(waits) == 0, idle_secs=30, busy_secs=5)
            mon.stop.wait = lambda secs: (waits.append(secs), mon.stop.set() if len(waits) == 2 else None)[-1]
            mon.run()
            self.assertEqual(waits, [5, 30])
            lines = [json.loads(l) for l in Path(d, "health.log").read_text().splitlines()]
            self.assertEqual([l["busy"] for l in lines], [True, False])

    def test_battery(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(h.battery(Path(d)))                              # no battery (desktop PC)
            Path(d, "ACAD").mkdir()
            Path(d, "ACAD", "type").write_text("Mains\n")
            Path(d, "BAT1").mkdir()
            Path(d, "BAT1", "type").write_text("Battery\n")
            Path(d, "BAT1", "capacity").write_text("23\n")
            Path(d, "BAT1", "status").write_text("Discharging\n")
            self.assertEqual(h.battery(Path(d)), {"percent": 23, "charging": False, "status": "Discharging"})
            Path(d, "BAT1", "status").write_text("Charging\n")
            self.assertTrue(h.battery(Path(d))["charging"])
            Path(d, "BAT1", "status").write_text("Full\n")
            Path(d, "BAT1", "capacity").write_text("104\n")
            self.assertEqual(h.battery(Path(d))["percent"], 100)
        self.assertIsNone(h.battery(Path("/nonexistent")))

    def test_budget(self):
        b = h.Budget(3, 60)
        self.assertEqual([b.take() for _ in range(5)], [True, True, True, False, False])


class Sync(unittest.TestCase):
    def test_full_sync_with_fake_services(self):
        with Sandbox() as sb:
            st = sb.state()
            st.sync()
            self.assertEqual(st.progress["phase"], "done")
            m = st.cache["matches"]
            self.assertEqual(m["legendary:alpha"]["appid"], 100)
            self.assertEqual(m["legendary:alpha"]["metascore"], 85)
            self.assertEqual(m["legendary:beta"]["appid"], 200)
            self.assertIsNone(m["legendary:gamma"].get("appid"))
            self.assertEqual(m["legendary:gamma"]["critic"], {"score": 81, "by": "OpenCritic", "n": 40})
            self.assertEqual(st.wikidata.calls, ["Gamma Online"], "only games Steam missed; editors are skipped")
            self.assertTrue(h.CACHE_FILE.exists())

    def test_second_sync_uses_the_cache(self):
        cache = synced_cache()
        with Sandbox(cache) as sb:
            st = sb.state()
            st.sync()
            self.assertEqual([c for c in st.steam.calls if c[0] == "items"], [], "fresh Steam data isn't fetched again")

    def test_reload_when_heroic_changes(self):
        with Sandbox(synced_cache()) as sb:
            st = sb.state()
            st.reload_library()
            st.set_progress("done")
            st._lib_checked = 0
            st.maybe_reload_library()                       # first call records the signature
            self.assertFalse(next(g for g in st.games if g["app_name"] == "beta")["installed"])
            sb.installed_file.write_text(json.dumps({"alpha": {}, "beta": {}}))
            os.utime(sb.installed_file, (time.time() + 5, time.time() + 5))
            st.maybe_reload_library(force=True)
            self.assertTrue(next(g for g in st.games if g["app_name"] == "beta")["installed"])


class Rematch(unittest.TestCase):
    def test_only_unmatched_games_are_retried_now(self):
        with Sandbox(synced_cache()) as sb:
            h.OVERRIDES_FILE.write_text(json.dumps({"legendary:beta": None}))   # hand-set "not on Steam": leave alone
            st = sb.state()
            st.reload_library()
            st.wake.clear()
            self.assertEqual(st.rematch_unmatched(), 1)                      # only Gamma (Ued is an editor)
            self.assertEqual((st.cache["matches"]["legendary:gamma"]["t"], st.cache["matches"]["legendary:gamma"]["wd_t"]), (0, 0))
            self.assertNotEqual(st.cache["matches"]["legendary:alpha"].get("t"), 0)
            self.assertTrue(st.wake.is_set())
            st.sync()
            self.assertEqual(st.wikidata.calls, ["Gamma Online"], "asked again straight away")
            self.assertIn(("search", "Gamma Online"), st.steam.calls)


class GameView(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(synced_cache()).__enter__()
        self.st = self.sb.state()
        self.st.reload_library()
        self.games = {g["app_name"]: g for g in self.st.snapshot()["games"]}

    def tearDown(self):
        self.sb.__exit__(None, None, None)

    def test_steam_game(self):
        a = self.games["alpha"]
        self.assertEqual((a["percent"], a["reviews"], a["label"]), (95, 20000, "Overwhelmingly Positive"))
        self.assertGreater(a["rating"], 90)
        self.assertEqual(a["genres"], ["Action", "Adventure"])
        self.assertEqual((a["year"], a["devs"], a["metascore"]), (2020, ["Alpha Studio"], 85))
        self.assertTrue(a["installed"])
        self.assertIn("steamstatic.com", a["art"])

    def test_critic_only_game_and_extras(self):
        g = self.games["gamma"]
        self.assertIsNone(g.get("percent"))
        self.assertEqual((g["rating"], g["critic"], g["critic_by"], g["critic_n"]), (81.0, 81, "OpenCritic", 40))
        self.assertTrue(self.games["ued"]["extra"])

    def test_igdb_fills_gaps_and_links_library(self):
        c = self.st.cache
        c["igdb_map"] = {"legendary:alpha": {"id": 1}, "legendary:gamma": {"id": 3}}
        c["igdb"] = {
            "1": {"id": 1, "critic": 70, "critic_n": 2, "user": 80, "user_n": 50, "modes": ["Single player"], "persp": [],
                  "series": ["Quest"], "similar": [3], "main": 12.0, "extra": 999.0, "comp": None},
            "3": {"id": 3, "critic": 90, "critic_n": 3, "user": 60, "user_n": 9, "modes": ["Multiplayer"], "persp": ["First person"],
                  "series": ["Quest"], "similar": [], "summary": "Online game.", "cover": "https://igdb/c.jpg", "year": 2021,
                  "devs": ["G Co"], "genres": ["Shooter"], "themes": ["Action", "Horror"]},
        }
        games = {g["app_name"]: g for g in self.st.snapshot()["games"]}
        a, g = games["alpha"], games["gamma"]
        self.assertEqual(a["length"], {"main": 12.0, "extra": None, "comp": None, "src": "igdb"})   # 999h placeholder dropped
        self.assertEqual(a["igdb"]["same_series"], ["legendary:gamma"])
        self.assertEqual(a["igdb"]["similar"], ["legendary:gamma"])
        self.assertEqual(a["critic_score"], 85)                         # Metascore beats a thin IGDB score
        # Gamma: OpenCritic (40 reviews) beats IGDB (3 reviews); IGDB fills the blanks
        self.assertEqual((g["critic"], g["critic_by"]), (81, "OpenCritic"))
        self.assertEqual((g["desc"], g["year"], g["devs"], g["art"]), ("Online game.", 2021, ["G Co"], "https://igdb/c.jpg"))
        self.assertEqual(g["genres"], ["Shooter", "Action", "Horror"])
        self.assertEqual(g["modes"], ["Multiplayer"])

    def test_videos_steam_only_without_igdb_key(self):
        vs = self.st.videos_for("legendary:alpha")
        self.assertEqual(vs, [{"src": "steam", "name": "Launch Trailer", "thumb": "t.jpg", "url": "v.m3u8"}])
        self.st.videos_for("legendary:alpha")
        self.assertEqual(sum(1 for c in self.st.steam.calls if c[0] == "get"), 1, "cached for a month")
        self.assertEqual(self.st.videos_for("legendary:gamma"), [])
        self.assertEqual(self.st.videos_for("nope"), [])


class Api(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(synced_cache()).__enter__()
        self.st = self.sb.state()
        self.st.reload_library()
        self.st.set_progress("done")
        self.srv = Server(self.st)
        self.logfile = h.setup_logging(h.LOG_DIR)
        self.saved = {k: getattr(h, k) for k in ("launch_game", "heroic_downloads", "ui_log_budget")}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(h, k, v)
        self.srv.close()
        for hd in [hd for hd in h.logger.handlers if isinstance(hd, logging.FileHandler)]:
            h.logger.removeHandler(hd)
            hd.close()
        self.sb.__exit__(None, None, None)

    def test_games_status_and_static_files(self):
        code, body = self.srv.get("/api/games")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(len(d["games"]), 4)
        self.assertFalse(d["lengths"])
        code, body = self.srv.get("/api/status")
        self.assertEqual(json.loads(body)["progress"]["phase"], "done")
        self.assertIn("battery", json.loads(body))
        code, body = self.srv.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<html", body)
        self.assertEqual(self.srv.get("/../heroic_ratings.py")[0], 404)
        self.assertEqual(self.srv.get("/missing.js")[0], 404)

    def test_bad_requests(self):
        self.assertEqual(self.srv.post("/api/launch", raw=b"{not json")[0], 400)
        self.assertEqual(self.srv.post("/api/launch", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.srv.post("/api/launch", {"id": "nope"})[0], 404)
        self.assertEqual(self.srv.post("/api/install", {"id": "legendary:beta"})[0], 400)

    def test_launch_is_logged(self):
        h.launch_game = lambda runner, app, d: {"via": "steam-shortcut"}
        code, r = self.srv.post("/api/launch", {"id": "legendary:alpha"})
        self.assertEqual((code, r["ok"], r["via"]), (200, True, "steam-shortcut"))
        self.assertIn('POST /api/launch {"id": "legendary:alpha"}', self.logfile.read_text())

    def test_crash_in_a_request_is_logged_with_traceback(self):
        def boom(*a):
            raise RuntimeError("launch exploded")
        h.launch_game = boom
        code, r = self.srv.post("/api/launch", {"id": "legendary:alpha"})
        self.assertEqual((code, r), (500, {"error": "internal error"}))
        text = self.logfile.read_text()
        self.assertIn("POST /api/launch crashed", text)
        self.assertIn("RuntimeError: launch exploded", text)

    def test_ui_errors_go_to_the_log_with_a_budget(self):
        h.ui_log_budget = h.Budget(2, 60)
        for i in range(4):
            self.assertEqual(self.srv.post("/api/log", {"level": "error", "message": f"bad thing {i}", "stack": "at app.js:1"})[0], 200)
        text = self.logfile.read_text()
        self.assertIn("UI: bad thing 0", text)
        self.assertIn("at app.js:1", text)
        self.assertNotIn("bad thing 2", text)

    def test_override_round_trip(self):
        self.assertEqual(self.srv.post("/api/override", {"id": "legendary:beta", "appid": 999})[0], 200)
        self.assertEqual(json.loads(h.OVERRIDES_FILE.read_text())["legendary:beta"], 999)
        self.srv.post("/api/override", {"id": "legendary:beta", "appid": None})
        self.assertIsNone(json.loads(h.OVERRIDES_FILE.read_text())["legendary:beta"])

    def test_downloads_mark_busy_only_when_heroic_is_working(self):
        h.heroic_downloads = lambda d: {"running": False}
        self.assertEqual(self.srv.post("/api/downloads")[1], {"running": False})
        self.assertLess(self.st.busy_until, time.time())
        h.heroic_downloads = lambda d: {"running": True, "queue": [{"appName": "beta", "runner": "legendary"}], "uninstalling": []}
        self.srv.post("/api/downloads")
        self.assertGreater(self.st.busy_until, time.time() + 60)

    def test_videos_and_game_shortcut(self):
        code, r = self.srv.post("/api/videos", {"id": "legendary:alpha"})
        self.assertEqual(r["videos"][0]["name"], "Launch Trailer")
        self.assertEqual(self.srv.post("/api/game-shortcut", {"id": "legendary:alpha"})[1]["status"], "missing")

    def test_steam_setup_removes_old_debug_shortcut(self):
        cfg = h.STEAM_USERDATA[0] / "1/config"
        cfg.mkdir(parents=True)
        (cfg / "shortcuts.vdf").write_bytes(h.vdf_dump({"shortcuts": {
            "0": {"appid": ("int", 1), "AppName": "Keep me"}, "1": {"appid": ("int", 2), "appname": "Heroic (Debug)"}}}))
        code, r = self.srv.post("/api/steam-setup")
        self.assertEqual((code, r["removed_old_shortcut"]), (200, True))
        self.assertEqual(list(h.vdf_load((cfg / "shortcuts.vdf").read_bytes())["shortcuts"].values()),
                         [{"appid": ("int", 1), "AppName": "Keep me"}])

    def test_twitch_settings_save_check_and_remove(self):
        from unittest import mock
        import urllib.error
        cid, secret = "abcdefghij0123456789", "s3cr3tvalue0123456789xyz"
        self.assertEqual(self.srv.post("/api/settings")[1]["twitch"], {"set": False, "source": None, "client_id_hint": ""})
        # rejected by Twitch: nothing stored
        def reject(self_):
            raise urllib.error.HTTPError("u", 400, "bad", {}, None)
        with mock.patch.object(h.IGDB, "_get_token", reject):
            code, r = self.srv.post("/api/settings/twitch", {"client_id": cid, "client_secret": secret})
        self.assertFalse(r["ok"])
        self.assertIn("HTTP 400", r["error"])
        self.assertFalse((h.DATA_DIR / "twitch.json").exists())
        # wrong format: not even sent to Twitch
        self.assertIn("doesn't look like", self.srv.post("/api/settings/twitch", {"client_id": "x y", "client_secret": "z"})[1]["error"])
        # accepted: stored privately, the secret never comes back or reaches the log
        with mock.patch.object(h.IGDB, "_get_token", lambda self_: None):
            code, r = self.srv.post("/api/settings/twitch", {"client_id": cid, "client_secret": secret})
        self.assertEqual((r["ok"], r["set"], r["source"], r["client_id_hint"]), (True, True, "file", "abcd…789"))
        f = h.DATA_DIR / "twitch.json"
        self.assertEqual(json.loads(f.read_text()), {"client_id": cid, "client_secret": secret})
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret, json.dumps(self.srv.post("/api/settings")[1]))
        self.assertNotIn(secret, self.logfile.read_text())
        self.assertTrue(self.st.wake.is_set(), "a sync starts to fetch IGDB data")
        # remove
        r = self.srv.post("/api/settings/twitch", {"remove": True})[1]
        self.assertEqual((r["ok"], r["set"]), (True, False))
        self.assertFalse(f.exists())

    def test_match_search_steam_igdb_and_app_id(self):
        from unittest import mock

        class FakeIGDB:
            def __init__(self, *a): pass
            def query(self, endpoint, body):
                return [{"id": 7, "name": "Alpha Quest", "first_release_date": 946684800}, {"id": 8, "name": "Alpha Quest Classic"}]
            def igdb_to_steam(self, ids):
                return {7: 100, 8: 150}            # 100 is also Steam's own result: listed once
        with mock.patch.object(h, "IGDB", FakeIGDB), mock.patch.object(h, "twitch_credentials", lambda: ("i", "s")):
            r = self.srv.post("/api/match-search", {"id": "legendary:gamma", "query": "Alpha"})[1]["results"]
        self.assertEqual([(x["appid"], x["src"]) for x in r], [(100, "Steam"), (150, "IGDB")])
        r = self.srv.post("/api/match-search", {"query": "200"})[1]["results"]             # a Steam app ID
        self.assertEqual([(x["appid"], x["name"], x["src"]) for x in r], [(200, "Bravo Racer", "Steam app ID")])
        self.assertEqual(self.srv.post("/api/match-search", {"query": "999999"})[1]["results"], [])
        self.assertEqual(self.srv.post("/api/match-search", {"query": "  "})[1]["results"], [])

    def test_quit(self):
        self.assertEqual(self.srv.post("/api/quit")[0], 200)
        self.assertTrue(self.srv.quit_called.wait(3))


if __name__ == "__main__":
    unittest.main()
