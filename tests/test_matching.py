import json, os, sys, tempfile, time, unittest
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
            (d / "store_cache/legendary_library.json").write_text(json.dumps(
                {"library": [{"app_name": "a1", "title": "Hades", "runner": "legendary",
                              "dlcList": [{"app_name": "x", "title": "DLC"}]}]}))
            (d / "store_cache/gog_library.json").write_text(json.dumps(
                {"games": [{"app_name": "g1", "title": "Frostpunk", "runner": "gog", "is_installed": True}]}))
            (d / "legendaryConfig/legendary/installed.json").write_text(json.dumps({"a1": {}}))
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

    def test_steam_start_time_from_pid_file(self):
        with tempfile.TemporaryDirectory() as d:
            saved = h.STEAM_PID_FILE
            try:
                h.STEAM_PID_FILE = Path(d, "steam.pid")
                h.STEAM_PID_FILE.write_text(str(os.getpid()))        # a process we can see: ourselves
                t = h.steam_start_time()
                self.assertIsNotNone(t)
                self.assertLess(abs(t - h._process_start(os.getpid())), 1)
                self.assertLessEqual(t, time.time())
                h.STEAM_PID_FILE.write_text("999999999")             # stale PID: not the running client
                self.assertNotEqual(h.steam_start_time(), t)
            finally:
                h.STEAM_PID_FILE = saved

    def test_shortcut_added_before_steam_restart_counts_as_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "shortcuts.vdf"); f.write_bytes(b"x")
            saved = (h.SHORTCUT_STATE_FILE, h.steam_start_time)
            h.SHORTCUT_STATE_FILE = Path(d, "state.json")
            entry = {"appid": ("int", 3589654731)}
            try:
                now = time.time()
                h.steam_start_time = lambda: now - 600            # Steam started 10 min ago, file newer
                self.assertFalse(h.shortcut_loaded_by_steam(entry, f))   # Trine first seen now: not loaded
                h.steam_start_time = lambda: now + 150            # Steam restarted after that
                self.assertTrue(h.shortcut_loaded_by_steam(entry, f))
            finally:
                h.SHORTCUT_STATE_FILE, h.steam_start_time = saved

    def test_launch_only_through_loaded_shortcut(self):
        saved = (h.game_shortcut_status, h.find_game_shortcut, h.steam_run, h.stop_our_heroic)
        ran = []
        h.steam_run = ran.append
        h.stop_our_heroic = lambda d: {}
        h.find_game_shortcut = lambda r, a: ({"appid": ("int", 5)}, Path("x"))
        try:
            for status, expected in [("missing", "no-shortcut"), ("restart", "restart-steam"), ("steam-not-running", "steam-not-running")]:
                h.game_shortcut_status = lambda r, a, s=status: s
                self.assertEqual(h.launch_game("legendary", "Eel", None), {"error": expected})
            self.assertEqual(ran, [])
            h.game_shortcut_status = lambda r, a: "ready"
            self.assertEqual(h.launch_game("legendary", "Eel", None), {"via": "steam-shortcut"})
            self.assertEqual(ran, [(5 << 32) | 0x02000000])
            h.stop_our_heroic = lambda d: {"error": "heroic-busy", "busy": ["legendary:X"]}   # downloading: don't launch
            self.assertEqual(h.launch_game("legendary", "Eel", None)["error"], "heroic-busy")
            self.assertEqual(len(ran), 1)
        finally:
            h.game_shortcut_status, h.find_game_shortcut, h.steam_run, h.stop_our_heroic = saved

    def test_igdb_steam_lookup_with_old_field_fallback(self):
        import urllib.error
        igdb = h.IGDB("id", "secret")
        calls = []
        def query(endpoint, body):
            calls.append(body)
            if "external_game_source" in body:
                raise urllib.error.HTTPError("u", 400, "bad field", {}, None)
            return [{"game": 77, "uid": "1794680"}]
        igdb.query = query
        self.assertEqual(igdb.steam_to_igdb([1794680, 22330]), {1794680: 77})
        self.assertEqual(igdb.source_field, "category")
        self.assertIn("category = 1", calls[1])

    def test_igdb_details_parsing(self):
        igdb = h.IGDB("id", "secret")
        def query(endpoint, body):
            if endpoint == "games":
                return [{"id": 77, "name": "Vampire Survivors", "rating": 81.2, "rating_count": 397,
                         "aggregated_rating": 90.6, "aggregated_rating_count": 5,
                         "game_modes": [{"id": 1, "name": "Single player"}, {"id": 3, "name": "Co-operative"}],
                         "player_perspectives": [{"id": 4, "name": "Bird view / Isometric"}],
                         "collections": [{"id": 9, "name": "Vampire Survivors"}], "similar_games": [5, 6],
                         "cover": {"id": 1, "image_id": "abc"}, "first_release_date": 1666224000,
                         "involved_companies": [{"developer": True, "company": {"name": "Poncle"}}],
                         "multiplayer_modes": [{"id": 1, "offlinecoop": True, "offlinecoopmax": 4}]}]
            return [{"game_id": 77, "hastily": 54000, "normally": 100800, "completely": 999 * 3600}]
        igdb.query = query
        d = igdb.details([77])[77]
        self.assertEqual((d["user"], d["user_n"], d["critic"], d["critic_n"]), (81, 397, 91, 5))
        self.assertEqual(d["modes"], ["Single player", "Co-operative"])
        self.assertEqual(d["mp"], {"offlinecoop": True, "offlinecoopmax": 4})
        self.assertEqual((d["main"], d["extra"], d["comp"]), (15.0, 28.0, None))   # 999h is a placeholder
        self.assertEqual((d["year"], d["devs"], d["series"]), (2022, ["Poncle"], ["Vampire Survivors"]))
        self.assertTrue(d["cover"].endswith("/abc.jpg"))

    def test_critic_score_choice(self):
        pick = lambda meta, wd, ig: (lambda o: (h.State._pick_critic(o, wd, ig), o)[1])({"metascore": meta, "percent": None})
        ig = lambda score, n: {"critic": score, "critic_n": n}
        # IGDB wins when solid and Wikidata doesn't have more reviews
        o = pick(None, {"score": 80, "by": "OpenCritic", "n": 4}, ig(85, 7))
        self.assertEqual((o["critic"], o["critic_by"], o["rating"]), (85, "IGDB", 85.0))
        # Wikidata (OpenCritic) wins with more reviews…
        o = pick(None, {"score": 83, "by": "OpenCritic", "n": 33}, ig(84, 7))
        self.assertEqual((o["critic"], o["critic_by"], o["critic_n"]), (83, "OpenCritic", 33))
        # …or when IGDB is thin (under 3 reviews), even with an unknown count
        o = pick(None, {"score": 72, "by": "OpenCritic", "n": None}, ig(50, 1))
        self.assertEqual(o["critic_by"], "OpenCritic")
        # thin IGDB and no Wikidata: no critic score
        self.assertNotIn("critic", pick(None, None, ig(100, 1)))
        # a Metascore comes first for the Critic score sort
        o = pick(76, {"score": 77, "by": "OpenCritic", "n": 59}, ig(74, 7))
        self.assertEqual((o["critic_score"], o["critic_src"], o["critic_by"]), (76, "Metascore", "OpenCritic"))

    def test_wikidata_review_count(self):
        claim = {"qualifiers": {"P7887": [{"datavalue": {"value": {"amount": "+33", "unit": "1"}}}]}}
        self.assertEqual(h._wd_review_count(claim), 33)
        self.assertIsNone(h._wd_review_count({"qualifiers": {}}))

    def test_igdb_videos_are_validated_youtube_ids(self):
        vs = h._igdb_videos([{"name": "Gameplay video", "video_id": "Yg9w4jRHX8A"},
                             {"name": "Trailer", "video_id": "bad id\"><script>"}, {"video_id": "WNNHAHZgKkU"}, "junk"])
        self.assertEqual(vs, [{"name": "Gameplay video", "id": "Yg9w4jRHX8A"}, {"name": "Video", "id": "WNNHAHZgKkU"}])

    def test_desktop_layout_detection(self):
        f = h.desktop_layout_likely
        self.assertTrue(f({"XDG_CURRENT_DESKTOP": "KDE"}, deck=True))       # Desktop Mode, started directly
        self.assertFalse(f({"XDG_CURRENT_DESKTOP": "KDE", "SteamGameId": "123"}, deck=True))  # started via Steam
        self.assertFalse(f({"XDG_CURRENT_DESKTOP": "gamescope"}, deck=True))  # Game Mode
        self.assertFalse(f({"XDG_CURRENT_DESKTOP": "KDE"}, deck=False))     # not a Deck


if __name__ == "__main__":
    unittest.main()
