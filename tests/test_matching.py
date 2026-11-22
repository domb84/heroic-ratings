import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import heroic_ratings as h

T = h.MATCH_THRESHOLD


class Matching(unittest.TestCase):
    def match(self, a, b):
        self.assertGreaterEqual(h.similarity(a, b), T, f"{a!r} should match {b!r}")

    def nomatch(self, a, b):
        self.assertLess(h.similarity(a, b), T, f"{a!r} should NOT match {b!r}")

    def test_editions_and_symbols(self):
        self.match("Batman™ Arkham Knight", "Batman™: Arkham Knight")
        self.match("Control Ultimate Edition", "CONTROL Ultimate Edition")
        self.match("Fallout: New Vegas Ultimate Edition", "Fallout: New Vegas")
        self.match("Tomb Raider GAME OF THE YEAR EDITION", "Tomb Raider Game of the Year")
        self.match("The Witcher 3: Wild Hunt - Game of the Year Edition", "The Witcher 3: Wild Hunt — Remastered")
        self.match("The Outer Worlds", "The Outer Worlds: Spacer's Choice Edition")
        self.match("Rise of the Tomb Raider: 20 Year Celebration", "Rise of the Tomb Raider™")
        self.match("Heroes of Might and Magic® 3: Complete", "Heroes Of Might And Magic III")
        self.match("Sid Meier's Civilization® VI", "Sid Meier’s Civilization® VI")

    def test_rejects(self):
        self.nomatch("Hollow Knight", "Hollow Knight: Silksong")
        self.nomatch("Assassin's Creed", "Assassin's Creed II")
        self.nomatch("Assassin's Creed", "Assassin's Creed® Unity")
        self.nomatch("The Outer Worlds", "The Outer Worlds 2")
        self.nomatch("Hades", "Hades II")
        self.nomatch("Celeste", "Celeste Soundtrack")
        self.nomatch("Alan Wake 2", "Beat Saber - Monstercat Mixtape 2 - Alan Walker")

    def test_queries_have_no_dash_operator(self):
        for q in h.search_queries("Disco Elysium - The Final Cut"):
            self.assertNotIn(" - ", q)


class Fallbacks(unittest.TestCase):
    def test_parse_score(self):
        self.assertEqual(h._parse_score("83/100"), 83)
        self.assertEqual(h._parse_score("8.6/10"), 86)
        self.assertIsNone(h._parse_score("86%"))      # OpenCritic "% recommend", not a score
        self.assertIsNone(h._parse_score(None))

    def test_extras(self):
        for t in ["Football Manager 2024 Pre-game editor", "Football Manager 2022 Resource Archiver",
                  "KillingFloor2Beta", "Chivalry 2 - Public Testing", "Q.U.B.E. 2 Soundtrack",
                  "Galactic Civilizations III (Test branch)", "Death Stranding Content"]:
            self.assertTrue(h.EXTRA_RE.search(t), t)
        for t in ["Football Manager 2024", "Hades", "Alan Wake 2", "Contractor: The Clean Up Part II", "Betrayal"]:
            self.assertFalse(h.EXTRA_RE.search(t), t)


class Library(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "store_cache").mkdir()
            (d / "legendaryConfig/legendary").mkdir(parents=True)
            json.dump({"library": [{"app_name": "a1", "title": "Hades", "runner": "legendary",
                                    "dlcList": [{"app_name": "x", "title": "DLC"}]}]},
                      open(d / "store_cache/legendary_library.json", "w"))
            json.dump({"games": [{"app_name": "g1", "title": "Frostpunk", "runner": "gog", "is_installed": True}]},
                      open(d / "store_cache/gog_library.json", "w"))
            json.dump({"a1": {}}, open(d / "legendaryConfig/legendary/installed.json", "w"))
            games = {g["id"]: g for g in h.load_library(d)}
            self.assertEqual(set(games), {"legendary:a1", "gog:g1"})
            self.assertTrue(games["legendary:a1"]["installed"])
            self.assertEqual(games["gog:g1"]["store"], "GOG")

    def test_weighted_rating(self):
        self.assertLess(h.weighted_rating(100, 5), h.weighted_rating(95, 100000))

    def test_debug_socket_is_local_only(self):
        with self.assertRaises(OSError):
            h._MiniWebSocket("ws://192.168.1.5:47317/devtools/page/x")
        self.assertIn("--remote-debugging-address=127.0.0.1", h.HEROIC_DEBUG_ARGS)
        self.assertIn("--no-gui", h.HEROIC_DEBUG_ARGS)

    def test_game_shortcut_url_parsing(self):
        e = {"exe": "flatpak", "LaunchOptions": 'run com.heroicgameslauncher.hgl --no-gui "heroic://launch?appName=Eel&runner=legendary"'}
        self.assertEqual(h._shortcut_game(e), ("legendary", "Eel"))
        self.assertEqual(h._shortcut_game({"exe": "x", "LaunchOptions": "heroic://launch/gog/1207658924"}), ("gog", "1207658924"))
        self.assertIsNone(h._shortcut_game({"exe": "flatpak", "LaunchOptions": "run com.heroicgameslauncher.hgl"}))

    def test_old_debug_shortcut_removed_and_others_kept(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d, "userdata/1/config"); cfg.mkdir(parents=True)
            game = {"appid": ("int", 123), "AppName": "Trine", "LaunchOptions": '"heroic://launch?appName=Eel&runner=legendary"', "tags": {}}
            old = {"appid": ("int", 456), "appname": "Heroic (Debug)", "LaunchOptions": "run x", "tags": {}}
            other = {"appid": ("int", 789), "AppName": "Other", "LaunchOptions": "", "tags": {"0": "fav"}}
            original = h.vdf_dump({"shortcuts": {"0": game, "1": old, "2": other}})
            (cfg / "shortcuts.vdf").write_bytes(original)
            self.assertEqual(h.vdf_dump(h.vdf_load(original)), original)
            saved = h.STEAM_USERDATA
            h.STEAM_USERDATA = [Path(d, "userdata")]
            try:
                self.assertEqual(len(h.remove_old_debug_shortcut()), 1)
                self.assertEqual(h.remove_old_debug_shortcut(), [])
                root = h.vdf_load((cfg / "shortcuts.vdf").read_bytes())
                self.assertEqual(root["shortcuts"], {"0": game, "1": other})   # renumbered, rest untouched
                self.assertEqual(len(list(cfg.glob("*.bak"))), 1)
                self.assertEqual(h.find_game_shortcut("legendary", "Eel")[0], game)
            finally:
                h.STEAM_USERDATA = saved

    def test_launch_only_through_loaded_shortcut(self):
        saved = (h.game_shortcut_status, h.find_game_shortcut, h.steam_run)
        ran = []
        h.steam_run = ran.append
        h.find_game_shortcut = lambda r, a: ({"appid": ("int", 5)}, Path("x"))
        try:
            for status, expected in [("missing", "no-shortcut"), ("restart", "restart-steam"), ("steam-not-running", "steam-not-running")]:
                h.game_shortcut_status = lambda r, a, s=status: s
                self.assertEqual(h.launch_game("legendary", "Eel", None), {"error": expected})
            self.assertEqual(ran, [])
            h.game_shortcut_status = lambda r, a: "ready"
            self.assertEqual(h.launch_game("legendary", "Eel", None), {"via": "steam-shortcut"})
            self.assertEqual(ran, [(5 << 32) | 0x02000000])
        finally:
            h.game_shortcut_status, h.find_game_shortcut, h.steam_run = saved

    def test_desktop_layout_detection(self):
        f = h.desktop_layout_likely
        self.assertTrue(f({"XDG_CURRENT_DESKTOP": "KDE"}, deck=True))       # Desktop Mode, started directly
        self.assertFalse(f({"XDG_CURRENT_DESKTOP": "KDE", "SteamGameId": "123"}, deck=True))  # started via Steam
        self.assertFalse(f({"XDG_CURRENT_DESKTOP": "gamescope"}, deck=True))  # Game Mode
        self.assertFalse(f({"XDG_CURRENT_DESKTOP": "KDE"}, deck=False))     # not a Deck


if __name__ == "__main__":
    unittest.main()
