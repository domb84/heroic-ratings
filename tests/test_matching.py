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


if __name__ == "__main__":
    unittest.main()
