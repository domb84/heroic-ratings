"""Browser tests for the interface (list, sorting, score tiles, filters, menus, error logging).

They run the real page in headless Chromium via Playwright, against the fixture library.
Skipped when Playwright or its browser isn't installed. To run them:

    python -m venv data/.test/venv && data/.test/venv/bin/pip install playwright
    PLAYWRIGHT_BROWSERS_PATH=data/.test/pw data/.test/venv/bin/playwright install chromium-headless-shell
    PLAYWRIGHT_BROWSERS_PATH=data/.test/pw data/.test/venv/bin/python -m unittest discover tests
"""
import json
import logging
import os
import time
import unittest
from pathlib import Path

from helpers import Sandbox, Server, synced_cache, h

ROOT = Path(__file__).resolve().parent.parent
if (ROOT / "data/.test/pw").is_dir():   # the local test setup; elsewhere (CI) Playwright's default location
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "data/.test/pw"))
try:
    from playwright.sync_api import sync_playwright
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(args=["--no-sandbox"])
except Exception as e:   # not installed, or no browser
    _browser, SKIP = None, f"Playwright not available ({type(e).__name__})"
else:
    SKIP = None


def tearDownModule():
    if _browser:
        _browser.close()
        _pw.stop()


@unittest.skipIf(SKIP, SKIP or "")
class Interface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox(synced_cache()).__enter__()
        cls.state = cls.sb.state()
        cls.state.reload_library()
        cls.state.set_progress("done")
        cls.srv = Server(cls.state)
        cls.logfile = h.setup_logging(h.LOG_DIR)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()
        for hd in [hd for hd in h.logger.handlers if isinstance(hd, logging.FileHandler)]:
            h.logger.removeHandler(hd)
            hd.close()
        cls.sb.__exit__(None, None, None)

    def setUp(self):
        self.page = _browser.new_page(viewport={"width": 1280, "height": 800})
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.add_init_script("localStorage.clear()")
        self.page.goto(self.srv.url + "/")
        self.page.wait_for_function("S.games.length > 0", timeout=10000)
        self.ev = self.page.evaluate
        self.ev("S.modal && closeModal()")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [], "no errors in the page")

    def rows(self):
        return self.ev("[...document.querySelectorAll('.row .name')].map(n => n.textContent)")

    def test_list_sorted_by_rating(self):
        self.assertEqual(self.rows(), ["Alpha Quest", "Gamma Online", "Bravo Racer", "Unreal Editor"])

    def test_first_tile_always_matches_the_list(self):
        for i in range(len(self.ev("SORTS"))):
            self.ev(f"setSort({i})")
            for title in ("Alpha Quest", "Bravo Racer", "Gamma Online"):
                self.ev(f"select(S.view.findIndex(g => g.title === {title!r}), true)")
                row = self.ev("document.querySelector('.row.sel .score .pct').textContent")
                tile = self.ev("document.querySelector('#detail .tiles div.cur b').textContent")
                self.assertEqual(row, tile, f"sort {i}, {title}")

    def test_tiles_show_each_score_once_with_counts(self):
        self.ev("setSort(0); select(S.view.findIndex(g => g.title === 'Alpha Quest'), true)")
        tiles = self.ev("[...document.querySelectorAll('#detail .tiles div')].map(d => d.innerText.replace(/\\n/g, ' / '))")
        self.assertEqual(tiles[1], "95% / Steam · 20k reviews")
        self.assertIn("85 / Metascore", tiles)
        self.ev("select(S.view.findIndex(g => g.title === 'Gamma Online'), true)")
        tiles = self.ev("[...document.querySelectorAll('#detail .tiles div')].map(d => d.innerText.replace(/\\n/g, ' / '))")
        self.assertEqual(tiles[0], "81 / Rating · OpenCritic · 40 reviews")
        self.assertEqual(sum("81" in t.split(" / ")[0] for t in tiles), 1, "the critic score isn't shown twice")

    def test_filters(self):
        self.ev("S.installedOnly = true; buildView()")
        self.assertEqual(self.rows(), ["Alpha Quest"])
        self.ev("S.installedOnly = false; S.genre = 'Racing'; buildView()")
        self.assertEqual(self.rows(), ["Bravo Racer"])

    def test_menu_and_genre_picker(self):
        self.ev("input('start')")
        labels = self.ev("S.modal.items().map(i => i.label)")
        for want in ("Installed only", "Settings…", "Quit"):
            self.assertIn(want, labels)
        for gone in ("Sort by", "Order", "Trailer sound", "Controller test", "Search…"):
            self.assertNotIn(gone, labels)
        self.ev("input('b')")
        self.assertFalse(self.ev("!!S.modal"))
        self.ev("input('b')")                                   # B: genre picker
        self.assertEqual(self.ev("S.modal.kind"), "grid")
        self.ev("input('b')")
        self.assertFalse(self.ev("!!S.modal"))

    def test_settings_page_enters_and_saves_twitch_credentials(self):
        from unittest import mock
        self.ev("S.steamDeck = false; input('start')")
        self.ev("S.modal.sel = S.modal.items().findIndex(i => i.label === 'Settings…'); input('a')")
        self.page.wait_for_function("S.modal && S.modal.title === 'Settings'")
        status = lambda: self.ev("S.modal.items().find(i => i.label === 'Status').value")
        self.assertEqual(status(), "Not set")
        for label, value in [("Twitch client ID", "abcdefghij0123456789"), ("Twitch client secret", "s3cr3tvalue0123456789xyz")]:
            self.ev(f"S.modal.sel = S.modal.items().findIndex(i => i.label === {label!r}); input('a')")
            self.page.wait_for_selector("#promptInput:focus")
            self.page.keyboard.type(value)
            self.page.keyboard.press("Enter")
            self.page.wait_for_function("S.modal && S.modal.title === 'Settings'")
        self.assertEqual(self.ev("S.modal.items().find(i => i.label === 'Twitch client secret').value"), "•" * 10 + " (new)")
        with mock.patch.object(h.IGDB, "_get_token", lambda self_: None):
            self.ev("S.modal.sel = S.modal.items().findIndex(i => i.label === 'Save and check with Twitch'); input('a')")
            self.page.wait_for_function("!S.modal")
        self.ev("openSettings()")
        self.page.wait_for_function("S.modal && S.modal.title === 'Settings'")
        self.assertEqual(status(), "Connected · abcd…789")
        self.assertNotIn("s3cr3t", self.page.content())
        self.ev("S.modal.sel = S.modal.items().findIndex(i => i.label === 'Remove credentials'); input('a')")
        self.page.wait_for_function("S.modal && S.modal.items().find(i => i.label === 'Status').value === 'Not set'")

    def test_y_searches_with_the_on_screen_keyboard_on_a_deck(self):
        self.ev("S.steamDeck = true; input('y')")
        self.assertEqual(self.ev("S.modal.kind"), "keyboard")
        # type "br" with the D-pad and A: 'b' is row 4 col 4, 'r' is row 2 col 3
        for a in ["down", "down", "right", "right", "right", "right", "a",        # row 1 -> 3, col 0 -> 4: 'b'
                  "up", "up", "left", "a"]:                                      # row 1, col 3: 'r'
            self.ev(f"input({a!r})")
        self.assertEqual(self.ev("S.modal.value"), "br")
        self.assertEqual(self.rows(), ["Bravo Racer"], "results update while typing")
        self.ev("input('y'); input('x')")                         # space then delete
        self.assertEqual(self.ev("S.modal.value"), "br")
        self.ev("input('b')")                                     # cancel restores the old search
        self.assertFalse(self.ev("!!S.modal"))
        self.assertEqual(len(self.rows()), 4)
        self.ev("input('y')")
        self.page.keyboard.type("alpha")                          # a physical keyboard types too
        self.page.keyboard.press("Enter")
        self.assertFalse(self.ev("!!S.modal"))
        self.assertEqual(self.rows(), ["Alpha Quest"])

    def test_y_focuses_the_search_box_on_a_pc(self):
        self.ev("S.steamDeck = false; input('y')")
        self.assertFalse(self.ev("!!S.modal"))
        self.assertTrue(self.ev("document.activeElement === els.search"))

    def test_settings_on_a_deck_use_the_on_screen_keyboard(self):
        self.ev("S.steamDeck = true; openSettings()")
        self.page.wait_for_function("S.modal && S.modal.title === 'Settings'")
        self.ev("S.modal.sel = S.modal.items().findIndex(i => i.label === 'Twitch client secret'); input('a')")
        self.assertEqual(self.ev("S.modal.kind"), "keyboard")
        self.page.keyboard.type("abc")
        self.assertIn("•••", self.ev("document.querySelector('.kbfield').textContent"))
        self.assertNotIn("abc", self.ev("document.querySelector('.kbfield').textContent"))
        self.ev("input('start')")                                 # Menu: done -> back to settings
        self.page.wait_for_function("S.modal && S.modal.title === 'Settings'")
        self.ev("settingsDraft.client_secret = ''; closeModal()")

    def test_pick_a_steam_match_for_an_unmatched_game(self):
        saved = json.loads(json.dumps(self.state.cache["matches"]["legendary:gamma"]))
        self.addCleanup(lambda: self.state.cache["matches"].__setitem__("legendary:gamma", saved))
        self.ev("S.steamDeck = false; select(S.view.findIndex(g => g.title === 'Gamma Online'), true); input('start')")
        item = self.ev("S.modal.items().find(i => i.label.startsWith('Steam match for'))")
        self.assertEqual((item["label"], item["value"]), ("Steam match for “Gamma Online”…", "none"))
        self.ev("S.modal.sel = S.modal.items().findIndex(i => i.label.startsWith('Steam match for')); input('a')")
        self.page.wait_for_selector("#promptInput:focus")
        self.page.fill("#promptInput", "Alpha")
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("S.modal && S.modal.items && S.modal.items().some(i => i.label === 'Alpha Quest')")
        self.assertEqual(self.ev("S.modal.items().find(i => i.label === 'Alpha Quest').value"), "Steam · app 100")
        self.ev("S.modal.sel = S.modal.items().findIndex(i => i.label === 'Alpha Quest'); input('a')")
        self.page.wait_for_function("!S.modal")
        deadline = time.time() + 5
        while not h.OVERRIDES_FILE.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(json.loads(h.OVERRIDES_FILE.read_text())["legendary:gamma"], 100)
        h.OVERRIDES_FILE.unlink()

    def test_trailer_keeps_playing_when_the_list_reloads(self):
        self.ev("select(S.view.findIndex(g => g.title === 'Alpha Quest'), true)")
        self.page.wait_for_function("document.querySelector('#vbox video')", timeout=5000)
        self.ev("window.__v = document.querySelector('#vbox video'); document.querySelector('#detail').scrollTop = 40")
        self.ev("S.version = -1"); self.ev("loadGames()")              # what a sync does every few seconds
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("document.querySelector('#vbox video') === window.__v && document.contains(window.__v)"),
                        "same video element, not restarted")
        self.ev("select(S.view.findIndex(g => g.title === 'Bravo Racer'), true)")
        self.assertFalse(self.ev("document.contains(window.__v)"), "another game: its own trailer")

    def test_menu_offers_matching_unmatched_games(self):
        self.ev("input('start')")
        label = self.ev("S.modal.items().find(i => i.label.startsWith('Match unmatched'))?.label")
        self.assertEqual(label, "Match unmatched games (1)")
        self.ev("input('b')")

    def test_battery_in_the_header(self):
        self.ev("showBattery({percent: 23, charging: false, status: 'Discharging'})")
        self.assertEqual(self.ev("$('batteryText').textContent"), "23%")
        self.assertFalse(self.ev("$('battery').hidden"))
        self.ev("showBattery({percent: 15, charging: false})")
        self.assertTrue(self.ev("$('battery').classList.contains('low')"))
        self.ev("showBattery({percent: 15, charging: true})")
        self.assertEqual(self.ev("$('batteryText').textContent"), "⚡15%")
        self.assertFalse(self.ev("$('battery').classList.contains('low')"), "not red while charging")
        self.ev("showBattery(null)")
        self.assertTrue(self.ev("$('battery').hidden"), "no battery: hidden")

    def test_wrap_around_at_the_ends_only_on_a_fresh_press(self):
        last = self.ev("S.view.length - 1")
        self.ev("select(0, true); input('up')")
        self.assertEqual(self.ev("S.sel"), last)
        self.ev("input('down')")
        self.assertEqual(self.ev("S.sel"), 0)
        self.ev("input('up', true)")                       # held: stops at the top
        self.assertEqual(self.ev("S.sel"), 0)
        self.ev(f"select({last}, true); input('down', true)")
        self.assertEqual(self.ev("S.sel"), last)

    def test_hold_lt_rt_jumps_to_top_and_bottom(self):
        page = _browser.new_page(viewport={"width": 1280, "height": 800})
        page.add_init_script("""
            window.__pad = { buttons: Array.from({ length: 17 }, () => ({ pressed: false, value: 0 })),
                             axes: [0, 0, 0, 0], connected: true, id: 'test pad', mapping: 'standard' };
            navigator.getGamepads = () => [window.__pad];
            Document.prototype.hasFocus = () => true;
            localStorage.clear();
        """)
        page.goto(self.srv.url + "/")
        page.wait_for_function("S.games.length > 0")
        ev = page.evaluate
        ev("S.modal && closeModal(); pageSize = () => 1; select(0, true)")    # 4 games: page by one to see it
        press = lambda i, ms: (ev(f"__pad.buttons[{i}].pressed = true"), page.wait_for_timeout(ms),
                               ev(f"__pad.buttons[{i}].pressed = false"), page.wait_for_timeout(120))
        press(7, 150)                                       # tap RT: one page
        self.assertEqual(ev("S.sel"), 1)
        press(7, 800)                                       # hold RT: bottom (once, not repeated paging)
        self.assertEqual(ev("S.sel"), ev("S.view.length - 1"))
        press(6, 800)                                       # hold LT: top
        self.assertEqual(ev("S.sel"), 0)
        ev("openMenu()")                                    # in a list: hold RT = last item
        press(7, 800)
        self.assertEqual(ev("S.modal.sel"), ev("S.modal.items().length - 1"))
        page.close()

    def test_page_errors_reach_the_app_log(self):
        self.ev("setTimeout(() => { throw new Error('ui test boom'); })")
        self.page.wait_for_timeout(500)
        self.errors.clear()                                     # the error was deliberate
        deadline = time.time() + 5
        while "ui test boom" not in self.logfile.read_text() and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("UI: page: ui test boom", self.logfile.read_text())


if __name__ == "__main__":
    unittest.main()
