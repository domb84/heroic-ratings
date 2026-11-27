"""Heroic 3.x (preliminary: written from the 3.0.0-beta.1 source, not yet tried on a device).

Compared with 2.22.4, everything this app uses is unchanged: the same window API
(names, arguments, result shapes), the same library files and launch links, --no-gui
and the same app.asar location in the Flatpak. What's new:

- Two stores. Steam (runner "steam", through Heroic's Aurelia helper; app_name is the
  Steam app ID) and itch.io (runner "itchio"). Both libraries sit in store_cache/ with
  is_installed on each entry.
- Heroic doesn't add Steam shortcuts for its Steam games: they're launched through Steam
  itself (steam://rungameid/<appid>).
- Installing and uninstalling Steam and itch.io games stays in Heroic for now: Steam
  games use Steam's compatdata prefixes (not Heroic's Wine prefixes), and itch.io goes
  through Butler. Epic, GOG, Amazon and sideloaded games work as with 2.x.
"""
from __future__ import annotations

from .v2 import Heroic2


class Heroic3(Heroic2):
    name = "Heroic 3.x"
    majors = (3,)
    tested = "3.0.0-beta.1"
    preliminary = True

    store_labels = {**Heroic2.store_labels, "steam": "Steam", "itchio": "itch.io"}
    LIBRARY_FILES = Heroic2.LIBRARY_FILES + [
        ("store_cache/steam_library.json", "steam"),
        ("store_cache/itchio_library.json", "itchio"),
    ]
    MANAGED = {"legendary", "gog", "nile", "sideload"}

    def manages(self, runner: str) -> bool:
        return runner in self.MANAGED

    def steam_appid(self, runner: str, app_name: str) -> int | None:
        return int(app_name) if runner == "steam" and str(app_name).isdigit() else None
