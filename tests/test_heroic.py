"""Heroic support: telling which Heroic is installed, picking the adapter for it, refusing
to drive a Heroic that isn't supported or has changed, and the adapter contract.

The contract tests run every adapter in version.ADAPTERS against a stand-in for that
Heroic version's window (tests/fixtures/heroicN_window.js) in headless Chromium, through
the same code path the app uses. Adding support for a new Heroic = a new adapter + a new
fixture + an entry in FIXTURES below.
"""
import json
import os
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import Sandbox, synced_cache, heroic_ctl, steam_api, library  # noqa: F401
from hr.heroic import version as heroic_version  # noqa: E402
from hr.heroic.v2 import Heroic2  # noqa: E402
from hr.heroic.v3 import Heroic3  # noqa: E402
from test_queue import CHROME  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# adapter class -> (stand-in window script, a version it's for)
WINDOWS = {Heroic2: ("heroic2_window.js", "2.22.4"), Heroic3: ("heroic3_window.js", "3.0.0-beta.1")}


def make_asar(path: Path, package: dict):
    """A minimal Electron app.asar holding package.json (the layout Heroic ships)."""
    body = json.dumps(package).encode()
    header = json.dumps({"files": {"package.json": {"offset": "0", "size": len(body)}}}).encode()
    pickle = struct.pack("<2I", len(header) + 4, len(header)) + header
    pickle += b"\0" * (-len(pickle) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<2I", 4, len(pickle)) + pickle + body)


class Versions(unittest.TestCase):
    def test_reads_the_version_from_heroics_app_asar(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d, "flatpak/resources/app.asar")
            make_asar(a, {"name": "heroic", "version": "2.22.4", "main": "build/main/main.js"})
            self.assertEqual(heroic_version.read_asar_version(a), "2.22.4")
            Path(d, "junk.asar").write_bytes(b"not an asar")
            with mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": ""}):
                self.assertEqual(heroic_version.detect_version([Path(d, "missing"), Path(d, "junk.asar"), a]), "2.22.4")
                self.assertIsNone(heroic_version.detect_version([Path(d, "missing")]))

    def test_picks_the_adapter_and_says_how_sure_it_is(self):
        cases = {"2.22.4": ("ok", Heroic2), "2.18.0": ("ok", Heroic2), "2.23.1": ("untested", Heroic2),
                 "3.0.0-beta.1": ("preliminary", Heroic3), "3.0.0": ("preliminary", Heroic3), "3.1.0": ("untested", Heroic3),
                 "4.0.0": ("unsupported", Heroic3), "1.10.3": ("unsupported", Heroic2),
                 None: ("unknown", Heroic2), "nonsense": ("unknown", Heroic2)}
        for v, (status, cls) in cases.items():
            a, s = heroic_version.pick(v)
            self.assertEqual(s, status, v)
            self.assertIs(type(a), cls, v)
        self.assertEqual(heroic_version.parse("3.0.0-beta.1"), (3, 0, 0))

    def test_every_adapter_has_a_contract_fixture(self):
        for cls in heroic_version.ADAPTERS:
            self.assertIn(cls, WINDOWS, f"{cls.__name__} needs a stand-in window in tests/fixtures")
            self.assertTrue(cls.majors and cls.tested and cls.required_api)


class Adapters(unittest.TestCase):
    """The parts that don't need Heroic's window."""
    def test_launch_urls_round_trip_and_old_forms_parse(self):
        for cls in heroic_version.ADAPTERS:
            a = cls()
            for runner, app in [("legendary", "Eel"), ("gog", "1207658924"), ("nile", "amzn1.adg.product.x y")]:
                self.assertEqual(a.parse_launch("flatpak run x " + a.launch_url(runner, app, False)), (runner, app))
        h = Heroic2()
        self.assertEqual(h.parse_launch("heroic://launch/gog/1207658924"), ("gog", "1207658924"))
        self.assertIsNone(h.parse_launch("run com.heroicgameslauncher.hgl"))

    def test_downloads_are_normalised(self):
        raw = {"state": "running", "queue": [{"appName": "a", "runner": "gog", "type": "install"}, "junk"],
               "progress": {"a": {"status": "installing", "progress": {"percent": 5, "downSpeed": 2.5, "eta": "1:00"}},
                            "b": {"status": "uninstalling"}},
               "uninstalling": [{"appName": "b", "runner": "legendary"}]}
        self.assertEqual(Heroic2().normalise_downloads(raw), {
            "state": "running", "queue": [{"appName": "a", "runner": "gog"}],
            "progress": {"a": {"percent": 5, "speed": 2.5, "eta": "1:00"}},
            "uninstalling": [{"appName": "b", "runner": "legendary"}]})
        self.assertEqual(Heroic2().normalise_downloads(None)["queue"], [])

    def test_heroic3_reads_steam_and_itch_libraries(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d)
            (h / "store_cache").mkdir()
            (h / "store_cache/legendary_library.json").write_text(json.dumps({"library": [{"app_name": "eel", "title": "Eel"}]}))
            (h / "store_cache/steam_library.json").write_text(json.dumps({"games": [
                {"app_name": "620", "title": "Portal 2", "runner": "steam", "is_installed": True}]}))
            (h / "store_cache/itchio_library.json").write_text(json.dumps({"library": [
                {"app_name": "itch-77", "title": "Celeste Classic", "runner": "itchio", "is_installed": False}]}))
            with mock.patch.object(heroic_ctl, "adapter", lambda: Heroic3()):
                games = {g["app_name"]: g for g in library.load_library(h)}
            self.assertEqual(set(games), {"eel", "620", "itch-77"})
            p, i, e = games["620"], games["itch-77"], games["eel"]
            self.assertEqual((p["store"], p["installed"], p["steam_appid"], p["managed"]), ("Steam", True, 620, False))
            self.assertEqual((i["store"], i["steam_appid"], i["managed"]), ("itch.io", None, False))
            self.assertEqual((e["store"], e["steam_appid"], e["managed"]), ("Epic", None, True))
            self.assertIn("store_cache/steam_library.json", Heroic3().watched_files())
            # the 2.x adapter doesn't know these stores
            with mock.patch.object(heroic_ctl, "adapter", lambda: Heroic2()):
                self.assertEqual({g["app_name"] for g in library.load_library(h)}, {"eel"})

    def test_library_comes_through_the_adapter(self):
        with Sandbox(synced_cache()) as sb:
            games = library.load_library(sb.heroic)
            self.assertEqual({g["app_name"] for g in games}, {"alpha", "beta", "gamma", "ued"})
            self.assertEqual({g["store"] for g in games}, {"Epic"})
            self.assertIn("legendaryConfig/legendary/installed.json", Heroic2().watched_files())


class SteamGamesFromHeroic(unittest.TestCase):
    def test_matched_by_app_id_without_searching(self):
        with Sandbox(synced_cache()) as sb:
            (sb.heroic / "store_cache/steam_library.json").write_text(json.dumps({"games": [
                {"app_name": "100", "title": "Alpha Quest (Steam)", "runner": "steam", "is_installed": True}]}))
            with mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": "3.0.0-beta.1"}):
                heroic_ctl.compat(refresh=True)
                self.addCleanup(heroic_ctl.compat, refresh=True)
                st = sb.state()
                st.reload_library()
                st.sync()
            m = st.cache["matches"]["steam:100"]
            self.assertEqual((m["appid"], m["via"]), (100, "heroic"))
            self.assertNotIn(("search", "Alpha Quest (Steam)"), st.steam.calls)
            g = next(g for g in st.snapshot()["games"] if g["id"] == "steam:100")
            self.assertEqual((g["store"], g["percent"], g["managed"]), ("Steam", 95, False))


class Compatibility(unittest.TestCase):
    def tearDown(self):
        heroic_ctl.compat(refresh=True)      # back to the version helpers.py sets

    def test_unsupported_heroic_is_never_driven(self):
        with mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": "4.0.0"}):
            heroic_ctl.compat(refresh=True)
            c = heroic_ctl.compatibility()
            self.assertEqual((c["version"], c["status"], c["installs"]), ("4.0.0", "unsupported", False))
            with mock.patch.object(heroic_ctl, "heroic_eval", side_effect=AssertionError("Heroic was touched")):
                r = heroic_ctl.install_options("legendary", "beta", "Windows", None)
                self.assertEqual(r["error"], "heroic-unsupported")
                self.assertIn("4.0.0", r["detail"])
                self.assertEqual(heroic_ctl.start_uninstall("legendary", "alpha", False, False, None)["error"], "heroic-unsupported")
            with mock.patch.dict(os.environ, {"HEROIC_RATINGS_ALLOW_UNSUPPORTED": "1"}):
                self.assertTrue(heroic_ctl.installs_allowed(), "developers can still try it")

    def test_heroic3_leaves_steam_and_itch_installs_to_heroic(self):
        with mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": "3.0.0-beta.1"}):
            heroic_ctl.compat(refresh=True)
            c = heroic_ctl.compatibility()
            self.assertEqual((c["status"], c["installs"], c["adapter"]), ("preliminary", True, "Heroic 3.x"))
            with mock.patch.object(heroic_ctl, "heroic_eval", side_effect=AssertionError("Heroic was touched")):
                for runner, store in (("steam", "Steam"), ("itchio", "itch.io")):
                    r = heroic_ctl.install_options(runner, "620", "Windows", None)
                    self.assertEqual(r["error"], "heroic-store-unmanaged")
                    self.assertIn(store, r["detail"])
                    self.assertEqual(heroic_ctl.start_uninstall(runner, "620", False, False, None)["error"], "heroic-store-unmanaged")

    def test_heroic3_steam_games_launch_through_steam(self):
        with mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": "3.0.0-beta.1"}):
            heroic_ctl.compat(refresh=True)
            ran = []
            with mock.patch.object(heroic_ctl, "stop_our_heroic", lambda d: {}), \
                    mock.patch.object(steam_api, "steam_run", ran.append), \
                    mock.patch.object(steam_api, "game_shortcut_status", side_effect=AssertionError("no shortcut needed")):
                self.assertEqual(heroic_ctl.launch_game("steam", "620", None), {"via": "steam-app"})
            self.assertEqual(ran, [620])

    def test_a_changed_api_turns_installs_off_once_noticed(self):
        heroic_ctl.compat(refresh=True)
        def fake_eval(d, js, timeout, what):
            if what == "Heroic API check":
                return {"value": ["getDMQueueInformation"]}
            raise AssertionError("ran a script against a Heroic that lacks what it needs")
        with mock.patch.object(heroic_ctl, "heroic_eval", fake_eval), \
                mock.patch.object(heroic_ctl, "_active_debug_port", lambda d, newer_than=0: 4242):
            r = heroic_ctl.uninstall_options("legendary", "alpha", None)
        self.assertEqual(r["error"], "heroic-api-changed")
        self.assertIn("getDMQueueInformation", r["detail"])
        c = heroic_ctl.compatibility()
        self.assertEqual((c["status"], c["installs"], c["missing"]), ("api-changed", False, ["getDMQueueInformation"]))


@unittest.skipUnless(CHROME, "headless Chromium (Playwright) not installed")
class Contract(unittest.TestCase):
    """Every adapter against a stand-in of its Heroic's window, through the app's own code."""

    def start_window(self, script: str):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "index.html").write_text(f"<script>{(FIXTURES / script).read_text()}</script>")
        profile = d / "profile"
        chrome = subprocess.Popen([CHROME, "--no-sandbox", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                                   f"--user-data-dir={profile}", (d / "index.html").as_uri()],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (chrome.kill(), chrome.wait()))
        deadline = time.time() + 10
        while not heroic_ctl._active_debug_port(profile) and time.time() < deadline:
            time.sleep(0.1)
        return profile

    def window(self, profile, js):
        return heroic_ctl.heroic_eval(profile, js, 10, "test")["value"]

    def test_contract(self):
        for cls, (script, ver) in WINDOWS.items():
            with self.subTest(adapter=cls.__name__), \
                    mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": ver}), \
                    mock.patch.object(heroic_ctl, "heroic_running", lambda: True):
                heroic_ctl.compat(refresh=True)
                self.addCleanup(heroic_ctl.compat, refresh=True)
                self.assertIsInstance(heroic_ctl.adapter(), cls)
                p = self.start_window(script)
                self.check_contract(p)

    def check_contract(self, p):
        # nothing running yet
        d = heroic_ctl.heroic_downloads(p)
        self.assertEqual((d["running"], d["queue"], d["uninstalling"]), (True, [], []))

        # install options: what our options screen needs
        o = heroic_ctl.install_options("legendary", "beta", "Windows", p)
        self.assertNotIn("error", o, o)
        self.assertEqual((o["title"], o["platforms"], o["platform"]), ("Bravo Racer: Deluxe", ["Windows", "linux"], "Windows"))
        self.assertEqual((o["download"], o["disk"], o["languages"]), (1000, 3000, ["en", "fr"]))
        self.assertEqual(o["dlcs"], [{"id": "beta_dlc", "title": "Bravo Soundtrack", "perLang": None}])
        self.assertEqual([w["bin"] for w in o["wine"]], ["/w/ge", "/w/wine"])
        self.assertEqual(o["wineDefault"], "/w/ge")
        self.assertEqual((o["prefixOwn"], o["prefixShared"]), ("/p/Bravo Racer Deluxe", "/p/default"))
        self.assertEqual(o["locations"][0]["path"], "/home/deck/Games/Heroic")
        self.assertEqual(heroic_ctl.install_options("legendary", "alpha", "Windows", p)["error"], "installed")

        # install: queued with the choices, Wine saved first; asking again doesn't queue twice
        choice = {"path": "/games", "platform": "Windows", "dlcs": ["beta_dlc"], "language": "fr", "wine": "/w/wine"}
        self.assertEqual(heroic_ctl.install_with_options("legendary", "beta", choice, p), {"result": "queued"})
        self.assertEqual(heroic_ctl.install_with_options("legendary", "beta", choice, p), {"result": "queued"})
        calls = self.window(p, "window.calls")
        self.assertEqual([c[0] for c in calls], ["writeConfig", "install"])
        cfg_call = calls[0][1]
        self.assertEqual((cfg_call["appName"], cfg_call["config"]["wineVersion"]["bin"], cfg_call["config"]["winePrefix"],
                          cfg_call["config"]["other"]), ("beta", "/w/wine", "/p/Bravo Racer Deluxe", 1))
        self.assertEqual(calls[1][1], {"appName": "beta", "runner": "legendary", "path": "/games", "platformToInstall": "Windows",
                                       "installDlcs": ["beta_dlc"], "installLanguage": "fr", "hasGameInfo": True})

        # progress, in our shape
        d = heroic_ctl.heroic_downloads(p)
        self.assertEqual(d["queue"], [{"appName": "beta", "runner": "legendary"}])
        self.assertEqual(d["progress"]["beta"], {"percent": 42, "speed": 12.5, "eta": "00:01:00"})

        # uninstall options and uninstall (doesn't wait for Heroic to finish)
        u = heroic_ctl.uninstall_options("legendary", "alpha", p)
        self.assertEqual((u["title"], u["installPath"], u["native"], u["prefix"], u["prefixShared"]),
                         ("Alpha Quest", "/games/Alpha", False, "/p/default", True))
        self.assertTrue(u["epicBusy"], "an Epic download is running")
        self.assertEqual(heroic_ctl.uninstall_options("legendary", "beta", p)["error"], "not-installed")
        t = time.time()
        self.assertEqual(heroic_ctl.start_uninstall("legendary", "alpha", True, False, p), {"result": "started"})
        self.assertLess(time.time() - t, 8)
        self.assertEqual(heroic_ctl.start_uninstall("legendary", "alpha", True, False, p), {"result": "started"},
                         "asking again while it runs doesn't start a second one")
        self.assertEqual(heroic_ctl.start_uninstall("legendary", "beta", True, False, p), {"result": "done"},
                         "not installed: nothing to do")
        self.assertEqual(self.window(p, "window.calls.filter(c => c[0] === 'uninstall').map(c => c[1])"),
                         [{"app": "alpha", "runner": "legendary", "prefix": True, "settings": False}])
        self.assertEqual(heroic_ctl.heroic_downloads(p)["uninstalling"], [{"appName": "alpha", "runner": "legendary"}])
        c = heroic_ctl.compatibility()
        self.assertEqual((c["installs"], c["missing"]), (True, []), "the API check passed")

    def test_a_window_missing_functions_is_caught(self):
        with mock.patch.dict(os.environ, {"HEROIC_RATINGS_HEROIC_VERSION": "2.22.4"}), \
                mock.patch.object(heroic_ctl, "heroic_running", lambda: True):
            heroic_ctl.compat(refresh=True)
            self.addCleanup(heroic_ctl.compat, refresh=True)
            p = self.start_window("heroic2_window.js")
            self.window(p, "delete window.api.getDMQueueInformation; true")
            heroic_ctl.compat(refresh=True)       # as if Heroic had just been updated
            r = heroic_ctl.install_options("legendary", "beta", "Windows", p)
            self.assertEqual(r["error"], "heroic-api-changed")
            self.assertEqual(heroic_ctl.compatibility()["missing"], ["getDMQueueInformation"])


if __name__ == "__main__":
    unittest.main()
