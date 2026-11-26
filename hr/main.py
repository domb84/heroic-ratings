"""Start-up: the server, background threads and the browser fallback (--kiosk)."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from . import config as cfg
from . import health
from . import igdb as igdb_api
from . import logs
from . import server
from . import state as app_state
from .config import APP_VERSION, HOME, STEAM_DECK
from .logs import log


# ------------------------------------------------------------------- kiosk browser

CHROMIUM_FLATPAKS = ["org.chromium.Chromium", "com.google.Chrome", "com.brave.Browser",
                     "com.microsoft.Edge", "io.github.ungoogled_software.ungoogled_chromium"]
FIREFOX_FLATPAKS = ["org.mozilla.firefox", "one.ablaze.floorp", "io.gitlab.librewolf-community"]
CHROMIUM_BINS = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "brave", "microsoft-edge"]
FIREFOX_BINS = ["firefox", "floorp", "librewolf"]


def _flatpak_apps() -> set[str]:
    if not shutil.which("flatpak"):
        return set()
    try:
        out = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                             capture_output=True, text=True, timeout=15).stdout
        return set(out.split())
    except (OSError, subprocess.SubprocessError):
        return set()


def browser_command(url: str) -> list[str] | None:
    custom = os.environ.get("HEROIC_RATINGS_BROWSER")
    if custom:
        return custom.split() + [url]
    chromium_flags = ["--kiosk", "--no-first-run", "--no-default-browser-check",
                      "--disable-features=Translate,MediaRouter", "--password-store=basic",
                      "--disable-session-crashed-bubble", "--noerrdialogs"]
    apps = _flatpak_apps()
    for app in CHROMIUM_FLATPAKS:
        if app in apps:
            prof = HOME / ".var/app" / app / "data/heroic-ratings-kiosk"
            return ["flatpak", "run", app, f"--user-data-dir={prof}", *chromium_flags, url]
    for b in CHROMIUM_BINS:
        if shutil.which(b):
            prof = cfg.DATA_DIR / "browser-profile"
            return [b, f"--user-data-dir={prof}", *chromium_flags, url]
    for app in FIREFOX_FLATPAKS:
        if app in apps:
            prof = HOME / ".var/app" / app / "data/heroic-ratings-kiosk"
            prof.mkdir(parents=True, exist_ok=True)
            return ["flatpak", "run", app, "--new-instance", "--profile", str(prof), "--kiosk", url]
    for b in FIREFOX_BINS:
        if shutil.which(b):
            prof = cfg.DATA_DIR / "firefox-profile"
            prof.mkdir(parents=True, exist_ok=True)
            return [b, "--new-instance", "--profile", str(prof), "--kiosk", url]
    return None


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.loads(r.read()).get("app") == "heroic-ratings"
    except Exception:
        return False


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("HEROIC_RATINGS_PORT", 8787)))
    ap.add_argument("--heroic-dir", help="Heroic config dir (auto-detected for Flatpak and native)")
    ap.add_argument("--kiosk", action="store_true", help="open a fullscreen browser; exit when it closes")
    ap.add_argument("--windowed", action="store_true", help=argparse.SUPPRESS)  # used by the Electron shell
    ap.add_argument("--no-sync", action="store_true", help="don't fetch from Steam (use cache only)")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    browser = None
    stop = threading.Event()

    def quit_all():
        time.sleep(0.3)
        if browser and browser.poll() is None:
            try:
                os.killpg(browser.pid, signal.SIGTERM)
            except OSError:
                pass
        stop.set()

    httpd = None
    if already_running(args.port):
        log(f"server already running on {url}")
        if not args.kiosk:
            return
    else:
        logfile = logs.setup_logging()
        log(f"starting Heroic Ratings {APP_VERSION} (data {cfg.DATA_DIR}, log {logfile}, Steam Deck {STEAM_DECK}, "
            f"Game Mode {os.environ.get('XDG_CURRENT_DESKTOP', '').lower() == 'gamescope'}, "
            f"via Steam {bool(os.environ.get('SteamGameId'))}, IGDB key {bool(igdb_api.twitch_credentials())})")
        state = app_state.State(args.heroic_dir)
        health.HealthMonitor(cfg.LOG_DIR / "health.log", busy=lambda: time.time() < state.busy_until).start()
        state.ops.start()
        if not args.no_sync:     # --no-sync: offline test runs
            state.updates.start()
        if args.no_sync:
            state.reload_library()
            state.set_progress("done")
        else:
            threading.Thread(target=state.worker, daemon=True).start()
        # The page gets the token in the URL fragment (never sent to the server) and returns it
        # with every API call. Electron passes its own; otherwise make one for this run.
        token = os.environ.get("HEROIC_RATINGS_TOKEN") or secrets.token_urlsafe(24)
        url = f"{url}#t={token}"
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), server.make_handler(state, quit_all, token))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        log(f"serving on {url.split('#')[0]}")
        print(f"[heroic-ratings] open {url}", file=sys.stderr, flush=True)   # with the token; not in the log file

    if args.kiosk:
        cmd = browser_command(url)
        if not cmd:
            log("no supported browser found; open", url, "manually")
        else:
            log("browser:", " ".join(cmd))
            browser = subprocess.Popen(cmd, start_new_session=True)
            threading.Thread(target=lambda: (browser.wait(), stop.set()), daemon=True).start()

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    if browser and browser.poll() is None:
        try:
            os.killpg(browser.pid, signal.SIGTERM)
        except OSError:
            pass
    if httpd:
        httpd.shutdown()
