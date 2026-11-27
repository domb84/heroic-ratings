"""Reading the Heroic library and installed state (the files themselves are the adapter's business)."""
from __future__ import annotations

from pathlib import Path

from . import matching
from . import heroic as heroic_ctl
from .config import HEROIC_DIRS
from .logs import log


# --------------------------------------------------------------- Heroic library

def find_heroic_dir(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None
    for d in HEROIC_DIRS:
        if heroic_ctl.adapter().looks_like_config_dir(d):
            return d
    return None


def load_library(heroic: Path) -> list[dict]:
    a = heroic_ctl.adapter()
    installed = a.installed_ids(heroic)
    games: dict[str, dict] = {}

    def add(e, default_runner):
        runner = e.get("runner") or default_runner
        app = str(e.get("app_name"))
        title = str(e.get("title") or app).strip()
        if not app or not title or e.get("install", {}).get("is_dlc"):
            return
        gid = f"{runner}:{app}"
        if gid in games:
            return
        # how good this entry is, if the store lists the same game twice (see _dedupe)
        quality = (bool(e.get("is_installed")) or gid in installed,
                   e.get("installable") is not False and not e.get("thirdPartyManagedApp"),
                   sum(1 for k in ("art_square", "art_cover", "developer", "description", "extra") if e.get(k)))
        games[gid] = {
            "_quality": quality,
            "id": gid,
            "app_name": app,
            "runner": runner,
            "store": a.store_labels.get(runner, runner.title()),
            "title": title,
            "installed": bool(e.get("is_installed")) or gid in installed,
            "art": e.get("art_square") or e.get("art_cover") or "",
            "art_wide": e.get("art_cover") or "",
            "steam_appid": a.steam_appid(runner, app),       # a Steam game itself (Heroic 3)
            "managed": a.manages(runner),                    # installs/uninstalls through this app
            "store_ids": a.store_ids(runner, e),             # for exact lookups (Wikidata, IGDB)
        }

    for e, runner in a.library_entries(heroic):
        add(e, runner)
    return sorted(_dedupe(list(games.values())), key=lambda g: g["title"].lower())


def _dedupe(games: list[dict]) -> list[dict]:
    """A store sometimes lists the same game twice (e.g. two Epic entitlements of "Shadow
    Tactics: Blades of the Shogun" under different IDs). Show one per store: the installed
    one, else one the store says can be installed, else the one with more details."""
    groups: dict[tuple, list[dict]] = {}
    for g in games:
        groups.setdefault((g["runner"], matching.norm(g["title"])), []).append(g)
    out = []
    for (runner, _), gs in groups.items():
        gs.sort(key=lambda g: g["_quality"], reverse=True)
        out.append(gs[0])
        for dup in gs[1:]:
            log(f"hiding duplicate {dup['id']} ({dup['title']!r}, quality {dup['_quality']}) "
                f"of {gs[0]['id']} ({gs[0]['title']!r}, quality {gs[0]['_quality']})")
    for g in out:
        g.pop("_quality", None)
    return out
