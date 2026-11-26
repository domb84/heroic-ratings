"""The local HTTP API (127.0.0.1 only; refuses other websites and programs)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:   # type hints only
    from .state import State

import hmac
import json
import logging
import logging.handlers
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from . import config as cfg
from . import health
from . import heroic as heroic_ctl
from . import igdb as igdb_api
from . import steam as steam_api
from . import util
from .config import APP_VERSION
from .logs import log, log_error


# ------------------------------------------------------------------- HTTP server

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}


QUIET_PATHS = {"/api/downloads", "/api/log", "/api/game-shortcut", "/api/videos"}


class Budget:
    """At most `n` events per `per` seconds (so a looping UI error can't flood the log)."""
    def __init__(self, n: int, per: float):
        self.n, self.per, self.times = n, per, []

    def take(self) -> bool:
        now = time.monotonic()
        self.times = [t for t in self.times if now - t < self.per]
        if len(self.times) >= self.n:
            return False
        self.times.append(now)
        return True


ui_log_budget = Budget(30, 60)


def make_handler(state: State, on_quit, token: str | None = None):
    """The HTTP API. It only answers this app's own page: requests must name the server
    as 127.0.0.1/localhost (blocks DNS rebinding), mustn't come from another website
    (Origin), POSTs must be JSON (blocks cross-site form posts) and, when a token is set,
    every /api/ call must carry it (X-Heroic-Ratings-Token)."""
    class Handler(BaseHTTPRequestHandler):
        def _refuse(self, path: str) -> str | None:
            port = self.server.server_address[1]
            ours = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host", "") not in ours:
                return "host"
            origin = self.headers.get("Origin")
            if origin is not None and origin.removeprefix("http://") not in ours:
                return "origin"
            if token and path.startswith("/api/") and path != "/api/ping" and \
                    not hmac.compare_digest(self.headers.get("X-Heroic-Ratings-Token", ""), token):
                return "token"
            return None

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            why = self._refuse(urllib.parse.urlparse(self.path).path)
            if why:
                log("refused GET", self.path, f"({why})", level=logging.WARNING)
                return self._json({"error": "forbidden"}, 403)
            try:
                self._get()
            except Exception as e:
                log_error("GET", self.path, "crashed:", repr(e), exc=e)
                self._json({"error": "internal error"}, 500)

        def do_POST(self):
            why = self._refuse(urllib.parse.urlparse(self.path).path)
            if not why and not (self.headers.get("Content-Type") or "").startswith("application/json"):
                why = "content type"
            if why:
                log("refused POST", self.path, f"({why})", level=logging.WARNING)
                return self._json({"error": "forbidden"}, 403)
            try:
                self._post()
            except Exception as e:
                log_error("POST", self.path, "crashed:", repr(e), exc=e)
                self._json({"error": "internal error"}, 500)

        def _get(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/ping":   # "is this Heroic Ratings?" (no data, no token needed)
                return self._json({"app": "heroic-ratings"})
            if path == "/api/games":
                return self._json(state.snapshot())
            if path == "/api/status":
                state.maybe_reload_library()
                with state.lock:
                    return self._json({"app": "heroic-ratings", "version": state.version,
                                       "progress": state.progress, "battery": health.battery(),
                                       "update": state.updates.available(),
                                       "heroic": heroic_ctl.compatibility()})
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (cfg.WEB_DIR / rel).resolve()
            if cfg.WEB_DIR.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, f.read_bytes(), CONTENT_TYPES.get(f.suffix, "application/octet-stream"))

        def _post(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "bad json"}, 400)
            if path not in QUIET_PATHS:   # actions, not the UI's polling
                log("POST", path, json.dumps({k: v for k, v in body.items() if k in ("id", "force", "add", "fix", "options",
                                                                                      "removePrefix", "removeSettings", "platform")})[:300])
            if path == "/api/log":   # errors from the UI
                if ui_log_budget.take():
                    lvl = logging.ERROR if body.get("level") == "error" else logging.WARNING
                    log("UI:", str(body.get("message", ""))[:500], ("\n" + str(body.get("stack", ""))[:2000]) if body.get("stack") else "", level=lvl)
                return self._json({"ok": True})
            if path in ("/api/install-options", "/api/install-start", "/api/uninstall-options", "/api/uninstall-start"):
                state.mark_busy()
            if path == "/api/steam-setup":    # once at start-up
                try:
                    removed = steam_api.remove_old_debug_shortcut()
                    steam_api.note_game_shortcuts()
                except (OSError, ValueError) as e:
                    return self._json({"ok": False, "error": str(e)}, 500)
                return self._json({"ok": True, "removed_old_shortcut": bool(removed)})
            if path == "/api/game-shortcut":
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                return self._json({"ok": True, "status": steam_api.game_shortcut_status(g["runner"], g["app_name"])})
            if path in ("/api/install-start", "/api/uninstall-start"):   # into the queue
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                if path == "/api/install-start":
                    o = body.get("options") or {}
                    if not isinstance(o, dict) or not isinstance(o.get("path"), str) or not o["path"].startswith("/"):
                        return self._json({"ok": False, "error": "bad-path"})
                    r = state.ops.add("install", g, o)
                else:
                    r = state.ops.add("uninstall", g, {"removePrefix": body.get("removePrefix") is True,
                                                       "removeSettings": body.get("removeSettings") is True})
                state.mark_busy()
                return self._json({"ok": "error" not in r, "result": "queued", **r})
            if path == "/api/queue-cancel":
                return self._json({"ok": state.ops.cancel(str(body.get("id", "")))})
            if path == "/api/install-options":
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                try:
                    r = heroic_ctl.install_options(g["runner"], g["app_name"], body.get("platform"), state.heroic)
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                return self._json({"ok": "error" not in r, **r})
            if path == "/api/uninstall-options":
                g = next((g for g in state.games if g["id"] == body.get("id", "")), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                try:
                    r = heroic_ctl.uninstall_options(g["runner"], g["app_name"], state.heroic)
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                return self._json({"ok": "error" not in r, **r})
            if path == "/api/reload-library":   # Heroic just finished an install/uninstall
                state.maybe_reload_library(force=True)
                return self._json({"ok": True})
            if path == "/api/settings":
                return self._json({"ok": True, "twitch": igdb_api.twitch_settings(), "version": APP_VERSION,
                                   "updates": state.updates.view(), "heroic": heroic_ctl.compatibility()})
            if path == "/api/update-install":
                return self._json(state.updates.start_install())
            if path == "/api/settings/updates":
                on = body.get("enabled") is True
                state.updates.set_enabled(on)
                if on:
                    threading.Thread(target=state.updates.check, daemon=True).start()
                return self._json({"ok": True, "updates": state.updates.view()})
            if path == "/api/settings/twitch-test":
                r = igdb_api.test_twitch_credentials()
                log("IGDB key test:", "ok" if r["ok"] else r["error"])
                return self._json(r)
            if path == "/api/settings/twitch":
                r = igdb_api.remove_twitch_credentials() if body.get("remove") else \
                    igdb_api.save_twitch_credentials(str(body.get("client_id", "")), str(body.get("client_secret", "")))
                if r["ok"]:
                    with state.lock:
                        state.version += 1
                    state.wake.set()   # sync now: fetch (or stop showing) IGDB data
                return self._json(r)
            if path == "/api/rematch-unmatched":
                return self._json({"ok": True, "count": state.rematch_unmatched()})
            if path == "/api/match-search":
                g = next((g for g in state.games if g["id"] == body.get("id")), None)
                return self._json({"ok": True, "results": state.match_candidates(str(body.get("query", ""))[:100],
                                                                                  g["title"] if g else "")})
            if path == "/api/videos":
                return self._json({"ok": True, "videos": state.videos_for(body.get("id", ""))})
            if path == "/api/downloads":
                d = heroic_ctl.heroic_downloads(state.heroic)
                d["jobs"] = state.ops.view()
                d["resumed"] = state.ops.resumed
                if d.get("queue") or d.get("uninstalling") or state.ops.pending():
                    state.mark_busy()
                return self._json(d)
            if path in ("/api/launch", "/api/install"):
                gid = body.get("id", "")
                g = next((g for g in state.games if g["id"] == gid), None)
                if not g:
                    return self._json({"error": "unknown game"}, 404)
                if os.environ.get("HEROIC_LAUNCH_CMD"):   # custom command: hand it the URL as-is
                    try:
                        util.spawn_detached(heroic_ctl.heroic_command(g["runner"], g["app_name"],
                                                      show_gui=path == "/api/install"))
                    except OSError as e:
                        return self._json({"error": str(e)}, 500)
                    return self._json({"ok": True})
                if path == "/api/install":   # installs go through /api/install-options + /api/install-start
                    return self._json({"error": "use /api/install-options"}, 400)
                if state.ops.pending():   # launching closes our Heroic, which would stop the queue
                    return self._json({"ok": False, "error": "heroic-busy"})
                try:
                    r = heroic_ctl.launch_game(g["runner"], g["app_name"], state.heroic)
                except OSError as e:
                    return self._json({"error": str(e)}, 500)
                log(path, g["title"], "->", r)
                return self._json({"ok": "error" not in r, **r})
            if path == "/api/refresh":
                state.force = bool(body.get("force"))
                state.wake.set()
                return self._json({"ok": True})
            if path == "/api/override":
                appid = body.get("appid")
                state.set_override(body.get("id", ""), int(appid) if appid else None)
                return self._json({"ok": True})
            if path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=on_quit, daemon=True).start()
                return
            self._json({"error": "not found"}, 404)

    return Handler
