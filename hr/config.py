"""Paths, settings and constants (read through this module so tests can redirect paths)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent   # the app folder (hr/ is inside it)
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path(os.environ.get("HEROIC_RATINGS_DATA", APP_DIR / "data"))
CACHE_FILE = DATA_DIR / "cache.json"


def app_version(app_dir: Path = APP_DIR) -> str:
    """The build's version (VERSION file in the AppImage), or git's description of this
    checkout when run from source, else "dev"."""
    try:
        return (app_dir / "VERSION").read_text().strip() or "dev"
    except OSError:
        pass
    try:
        r = subprocess.run(["git", "describe", "--tags", "--always", "--dirty"], cwd=app_dir,
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() or "dev"
    except (OSError, subprocess.SubprocessError):
        return "dev"


APP_VERSION = app_version()
OVERRIDES_FILE = DATA_DIR / "overrides.json"
HOME = Path.home()
HEROIC_FLATPAK_ID = "com.heroicgameslauncher.hgl"
HEROIC_DIRS = [
    HOME / ".var/app" / HEROIC_FLATPAK_ID / "config/heroic",
    HOME / ".config/heroic",
]

# (which files hold the library, and the store names, depend on Heroic's version: hr/heroic/)

MATCH_THRESHOLD = 0.88
STEAM_REFRESH_SECS = 7 * 86400      # re-fetch review scores weekly
NOMATCH_RETRY_SECS = 30 * 86400     # retry games not found on Steam monthly
TAGLIST_REFRESH_SECS = 30 * 86400

# Steam user tags that describe a genre (used for the genre filter).
GENRE_TAGS = [
    "Action", "Adventure", "RPG", "Strategy", "Simulation", "Puzzle", "Platformer",
    "Shooter", "FPS", "Third-Person Shooter", "Roguelike", "Roguelite", "Metroidvania",
    "Survival", "Horror", "Survival Horror", "Racing", "Sports", "Fighting", "Open World",
    "Souls-like", "Action RPG", "JRPG", "CRPG", "Tactical RPG", "Turn-Based Strategy",
    "Turn-Based Tactics", "RTS", "Grand Strategy", "4X", "City Builder", "Colony Sim",
    "Management", "Base Building", "Tower Defense", "Card Game", "Deckbuilding",
    "Card Battler", "Visual Novel", "Point & Click", "Stealth", "Hack and Slash",
    "Beat 'em up", "Bullet Hell", "Shoot 'Em Up", "Twin Stick Shooter", "Arcade",
    "Casual", "Sandbox", "Crafting", "Exploration", "Walking Simulator",
    "Story Rich", "Immersive Sim", "Party Game", "MMORPG", "Battle Royale", "MOBA",
    "Farming Sim", "Life Sim", "Space", "Driving", "Flight", "Rhythm", "Mystery",
    "Detective", "Interactive Fiction", "Narrative", "Hidden Object", "Indie",
    "Free to Play", "Co-op", "Online Co-Op", "Local Co-Op", "Multiplayer",
    "Puzzle Platformer", "Precision Platformer", "3D Platformer", "2D Platformer",
    "Dungeon Crawler", "Looter Shooter", "Automation", "Economy", "Wargame",
    "Psychological Horror", "Action-Adventure",
]
GENRE_SET = set(GENRE_TAGS)
# IGDB scores based on very few reviews (median: 4 critics) aren't comparable with a
# Metascore or a Steam percentage, so they only count from these review counts up.
IGDB_MIN_CRITICS = 3
IGDB_MIN_USERS = 5
IGDB_GENRES = {
    "Role-playing (RPG)": "RPG", "Shooter": "Shooter", "Platform": "Platformer", "Puzzle": "Puzzle",
    "Racing": "Racing", "Sport": "Sports", "Fighting": "Fighting", "Strategy": "Strategy",
    "Real Time Strategy (RTS)": "RTS", "Turn-based strategy (TBS)": "Turn-Based Strategy",
    "Tactical": "Turn-Based Tactics", "Adventure": "Adventure", "Indie": "Indie", "Arcade": "Arcade",
    "Simulator": "Simulation", "Visual Novel": "Visual Novel", "Point-and-click": "Point & Click",
    "Card & Board Game": "Card Game", "Hack and slash/Beat 'em up": "Hack and Slash", "MOBA": "MOBA",
    "Music": "Rhythm", "Action": "Action", "Horror": "Horror", "Survival": "Survival", "Open world": "Open World",
    "Stealth": "Stealth", "Sandbox": "Sandbox",
}

UA = "heroic-ratings/1.0 (local library tool)"


# --------------------------------------------------------------------------- utils

# ------------------------------------------------------------------- logging
#
# Everything goes to stderr as before and, once setup_logging() has run, to
# <data>/logs/app.log (rotated: 1 MB x 4). health.log is written by HealthMonitor.

LOG_DIR = DATA_DIR / "logs"


# ------------------------------------------------------------------- game lengths
#
# IGDB, which needs a free Twitch developer app (client ID + secret). It fills gaps
# Steam leaves: critic and user scores, play styles and perspectives, series, similar
# games, multiplayer details, how long to beat, and covers/summaries/genres for games
# that aren't on Steam. Steam stays the main source wherever it has data.

IGDB_REFRESH_SECS = 30 * 86400
IGDB_MISS_RETRY_SECS = 14 * 86400


def is_steam_deck() -> bool:
    """True on Steam Deck hardware (LCD "Jupiter" / OLED "Galileo")."""
    try:
        vendor = Path("/sys/class/dmi/id/sys_vendor").read_text().strip()
        product = Path("/sys/class/dmi/id/product_name").read_text().strip()
    except OSError:
        return False
    return vendor == "Valve" and product in ("Jupiter", "Galileo")


STEAM_DECK = is_steam_deck()


def desktop_layout_likely(env=os.environ, deck: bool = STEAM_DECK) -> bool:
    """True when Steam is probably applying its Desktop controller layout to us.

    That happens on a Deck in Desktop Mode when the app wasn't started through
    Steam. Game Mode (gamescope) and Steam launches (SteamGameId set) give the
    app a gamepad layout instead.
    """
    if not deck:
        return False
    game_mode = env.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope" or "GAMESCOPE_WAYLAND_DISPLAY" in env
    via_steam = bool(env.get("SteamGameId") or env.get("SteamAppId"))
    return not game_mode and not via_steam


DESKTOP_LAYOUT = desktop_layout_likely()


# ------------------------------------------------------------------- Steam shortcuts
#
# In Game Mode, Steam treats everything started under our shortcut as one game, so a
# game launched through a Heroic we started would run inside our app and die with it.
# Games are therefore launched only through their own Steam shortcuts (Heroic adds one
# per installed game). Steam reads shortcuts.vdf only when it starts, so a game
# installed since then needs a Steam restart before it can be launched.
#
# Heroic itself (for install options, installs, uninstalls and progress) runs hidden
# as part of this app, with Chromium's debug port, which the app uses to drive it.

STEAM_USERDATA = ([Path(os.environ["HEROIC_RATINGS_STEAM_USERDATA"])] if os.environ.get("HEROIC_RATINGS_STEAM_USERDATA")
                  else [HOME / ".local/share/Steam/userdata", HOME / ".steam/steam/userdata"])
SHORTCUT_STATE_FILE = DATA_DIR / "steam-shortcut.json"


STEAM_PID_FILE = Path(os.environ.get("HEROIC_RATINGS_STEAM_PID", HOME / ".steam/steam.pid"))
