"""Shared test fixtures: a throwaway Heroic library + app data folder, and fake web services.

Nothing here touches the real Heroic/Steam folders or the network.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["HEROIC_RATINGS_HEROIC_VERSION"] = "2.22.4"   # not whatever Heroic this machine has
# the app's modules, under the same names the app uses for them
from hr import (config as cfg, logs, util, library, matching, steam as steam_api, wikidata,  # noqa: E402,F401
                igdb as igdb_api, heroic as heroic_ctl, ops, health, state as app_state, server)

# Three games and one editor. Alpha is installed and has full Steam + IGDB data,
# Beta has Steam data, Gamma isn't on Steam (critic score from Wikidata).
LIBRARY = [
    {"app_name": "alpha", "title": "Alpha Quest", "runner": "legendary"},
    {"app_name": "beta", "title": "Bravo Racer", "runner": "legendary"},
    {"app_name": "gamma", "title": "Gamma Online", "runner": "legendary"},
    {"app_name": "ued", "title": "Unreal Editor", "runner": "legendary"},
]
STEAM_ITEMS = {
    100: {"appid": 100, "success": 1, "name": "Alpha Quest",
          "reviews": {"summary_filtered": {"percent_positive": 95, "review_count": 20000, "review_score_label": "Overwhelmingly Positive"}},
          "tags": [{"tagid": 19, "weight": 9}, {"tagid": 21, "weight": 5}],
          "basic_info": {"short_description": "An adventure.", "developers": [{"name": "Alpha Studio"}]},
          "release": {"steam_release_date": 1577836800}},
    200: {"appid": 200, "success": 1, "name": "Bravo Racer",
          "reviews": {"summary_filtered": {"percent_positive": 70, "review_count": 300, "review_score_label": "Mostly Positive"}},
          "tags": [{"tagid": 699, "weight": 9}], "basic_info": {}, "release": {}},
}
TAGS = {"19": "Action", "21": "Adventure", "699": "Racing"}


class FakeSteam:
    """Stands in for heroic_ratings.Steam: store search, GetItems and the tag list."""
    def __init__(self):
        self.calls = []

    def search(self, term):
        self.calls.append(("search", term))
        names = {"alpha": (100, "Alpha Quest", 85), "bravo": (200, "Bravo Racer", None)}
        for key, (appid, name, meta) in names.items():
            if key in term.lower():
                return [{"id": appid, "name": name, "type": "app", "metascore": str(meta or "")}]
        return []

    def items(self, appids):
        self.calls.append(("items", tuple(appids)))
        return [STEAM_ITEMS[a] for a in appids if a in STEAM_ITEMS]

    def tag_names(self):
        return dict(TAGS)

    def get(self, url, params, timeout=20):
        self.calls.append(("get", url, params.get("appids")))
        if "appdetails" in url:
            a = str(params["appids"])
            return {a: {"data": {"movies": [{"name": "Launch Trailer", "thumbnail": "t.jpg", "hls_h264": "v.m3u8", "highlight": True},
                                            {"name": "No HLS", "thumbnail": "x.jpg"}]}}}
        return {}


class FakeWikidata:
    def __init__(self):
        self.calls = []

    def lookup(self, title):
        self.calls.append(title)
        if "gamma" in title.lower():
            return {"qid": "Q1", "name": "Gamma Online", "sim": 1.0, "steam": None,
                    "critic": {"score": 81, "by": "OpenCritic", "n": 40}, "metacritic": None}
        return None


class Sandbox:
    """Context manager: a temp Heroic dir + data dir, with the module's paths pointed at it."""
    PATCH = ("DATA_DIR", "CACHE_FILE", "OVERRIDES_FILE", "SHORTCUT_STATE_FILE", "LOG_DIR", "STEAM_USERDATA")

    def __init__(self, cache: dict | None = None):
        self.cache = cache

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.heroic = root / "heroic"
        self.data = root / "data"
        (self.heroic / "store_cache").mkdir(parents=True)
        (self.heroic / "legendaryConfig/legendary").mkdir(parents=True)
        self.data.mkdir()
        (self.heroic / "store_cache/legendary_library.json").write_text(json.dumps({"library": LIBRARY}))
        self.installed_file = self.heroic / "legendaryConfig/legendary/installed.json"
        self.installed_file.write_text(json.dumps({"alpha": {}}))
        self.saved = {k: getattr(cfg, k) for k in self.PATCH}
        cfg.DATA_DIR = self.data
        cfg.CACHE_FILE = self.data / "cache.json"
        cfg.OVERRIDES_FILE = self.data / "overrides.json"
        cfg.SHORTCUT_STATE_FILE = self.data / "steam-shortcut.json"
        cfg.LOG_DIR = self.data / "logs"
        cfg.STEAM_USERDATA = [root / "steam/userdata"]
        if self.cache is not None:
            cfg.CACHE_FILE.write_text(json.dumps(self.cache))
        return self

    def state(self, fake=True) -> "app_state.State":
        st = app_state.State(str(self.heroic))
        if fake:
            st.steam, st.wikidata = FakeSteam(), FakeWikidata()
        return st

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(cfg, k, v)
        self.tmp.cleanup()


def synced_cache() -> dict:
    """A cache as it looks after a sync of LIBRARY (built by running the real sync once)."""
    with Sandbox() as sb:
        st = sb.state()
        st.sync()
        return json.loads(json.dumps(st.cache))


class Server:
    """The app's real HTTP server on a free port, for a given State (and optional token,
    which get/post then send like the app's page does)."""
    def __init__(self, state, token: str | None = None):
        self.quit_called = threading.Event()
        self.token = token
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(state, self.quit_called.set, token))
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def request(self, path, method="GET", data: bytes | None = None, headers: dict | None = None):
        hdrs = {"X-Heroic-Ratings-Token": self.token} if self.token else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get(self, path, headers=None):
        return self.request(path, headers=headers)

    def post(self, path, body=None, raw: bytes | None = None, headers=None):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        code, out = self.request(path, "POST", data, {"Content-Type": "application/json", **(headers or {})})
        return code, json.loads(out or b"{}")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
