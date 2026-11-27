"""What a Heroic adapter provides: everything that depends on Heroic's version.

The rest of the app never talks to Heroic directly. It goes through the active adapter
(picked from the installed Heroic's version, see version.py), and only ever sees the
plain shapes documented below. To support a new Heroic version:

1. Copy v2.py to e.g. v3.py and subclass HeroicAdapter (or Heroic2 if most still works).
2. Set `name`, `majors` and `tested`, then change what's different: the library files,
   the launch URL, the window API it calls (`required_api`) and the scripts.
3. Add the class to ADAPTERS in version.py (newest first).
4. Add its fixtures to tests/test_heroic.py: the shared contract tests run against
   every adapter.

Scripts run inside Heroic's own window (its renderer, over Chromium's debug port), so
they can use whatever Heroic's preload exposes (in 2.x, `window.api`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


class HeroicAdapter:
    name = "Heroic"
    majors: tuple[int, ...] = ()          # major versions this adapter is written for
    tested = "0"                          # newest version it has been checked against
    preliminary = False                   # written from Heroic's source, not yet checked on a device

    # ---------------------------------------------------------------- files
    store_labels: dict[str, str] = {}     # runner id -> store name shown in the app

    def library_entries(self, heroic_dir: Path) -> Iterable[tuple[dict, str]]:
        """(entry, runner) for every game Heroic knows. An entry has at least app_name and
        title; optional: runner, is_installed, installable, thirdPartyManagedApp,
        art_square, art_cover, developer, description, extra, install.is_dlc."""
        raise NotImplementedError

    def installed_ids(self, heroic_dir: Path) -> set[str]:
        """"runner:app_name" of every installed game."""
        raise NotImplementedError

    def watched_files(self) -> list[str]:
        """Files (relative to Heroic's config folder) that change when the library or
        installed games change: the app re-reads the library when one does."""
        raise NotImplementedError

    def looks_like_config_dir(self, d: Path) -> bool:
        raise NotImplementedError

    # ---------------------------------------------------------------- per store
    def manages(self, runner: str) -> bool:
        """Whether this app installs and uninstalls this store's games through Heroic
        (otherwise the user is asked to do it in Heroic itself)."""
        return True

    def steam_appid(self, runner: str, app_name: str) -> int | None:
        """The Steam app ID when the game *is* a Steam game (no title matching needed; it's
        launched through Steam directly rather than through a Heroic-made shortcut)."""
        return None

    # ---------------------------------------------------------------- launch URLs
    def launch_url(self, runner: str, app_name: str, show_gui: bool) -> str:
        raise NotImplementedError

    def parse_launch(self, text: str) -> tuple[str, str] | None:
        """(runner, app_name) from a Steam shortcut's command line Heroic wrote, or None."""
        raise NotImplementedError

    # ---------------------------------------------------------------- Heroic's window
    required_api: tuple[str, ...] = ()    # window API functions the scripts below use
    ready_js = "document.readyState === 'complete'"   # true once Heroic's UI has loaded

    def probe_js(self) -> str:
        """Script: the required API functions that are missing (empty list: all there)."""
        return ("(() => { const api = window.api || {}; return %s.filter(n => typeof api[n] !== 'function'); })()"
                % json.dumps(list(self.required_api)))

    def install_options_js(self, runner: str, app_name: str, platform: str) -> str:
        """Script -> {title, platforms: ["Windows", "linux"], platform, download, disk (bytes),
        perLang, languages: [..], dlcs: [{id, title, perLang}], sdl: [..], defaultPath,
        wine: [{name, type, bin}], wineDefault (bin), uiLangs, prefixOwn, prefixShared}
        or {error: "unknown-game" | "installed" | "no-install-info" | "not-ready"}."""
        raise NotImplementedError

    def install_js(self, runner: str, app_name: str, choice: dict) -> str:
        """Queue an install. choice: {path, platform, dlcs, sdl, language, wine (bin or
        None), sharedPrefix}. Script -> "queued" (also when it's already queued: jobs are
        retried after a restart) | "installed" | "unknown-game" | "not-ready"."""
        raise NotImplementedError

    def uninstall_options_js(self, runner: str, app_name: str) -> str:
        """Script -> {title, installPath, native, prefix, prefixShared, epicBusy}
        or {error: "unknown-game" | "not-installed" | "not-ready"}."""
        raise NotImplementedError

    def uninstall_js(self, runner: str, app_name: str, remove_prefix: bool, remove_settings: bool) -> str:
        """Start an uninstall without waiting for it. Script -> "started" (also when it's
        already running) | "done" (finished, or wasn't installed) | "error" | "not-ready"."""
        raise NotImplementedError

    def downloads_js(self) -> str:
        """Script -> Heroic's activity, any shape; normalise_downloads() turns it into ours."""
        raise NotImplementedError

    def normalise_downloads(self, raw) -> dict:
        """-> {state: "running" | "paused" | "idle" | ..., queue: [{appName, runner}] (first
        is downloading), progress: {appName: {percent, speed (MiB/s), eta}},
        uninstalling: [{appName, runner}]}."""
        raise NotImplementedError
