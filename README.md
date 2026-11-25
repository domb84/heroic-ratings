# Heroic Ratings

Browse your Heroic Games Launcher library (Epic, GOG and Amazon games, plus sideloaded ones) by rating. Built for the Steam Deck: it works in Game Mode, with a controller, at 1280×720/800.

- **Ratings come from Steam, for free and with no API key.** Each game is matched to its Steam page. The app shows the Steam user-review score, a weighted rating (SteamDB-style, so a game with 5 reviews at 100% doesn't beat one with 500k reviews at 96%), and the Metascore.
- **Wikidata covers the gaps, also free with no API key.** Steam's search only finds games that are currently for sale. For any game it misses, the app asks Wikidata, which finds delisted Steam games (old Football Managers, Rocket League) so they still get their Steam reviews. For games not on Steam at all (Fortnite, Alan Wake 2), Wikidata supplies the Metacritic or OpenCritic critic score, shown as "Critics · OpenCritic".
- **IGDB extras** (with a free Twitch developer key): better matching, critic scores for more games, how long to beat, play-style and perspective filters, and same-series / similar games from your library.
- **Genre filter** uses Steam's genre tags.
- **Sort** by weighted rating, % positive, popularity, critic score, release year or title. The list shows the value you're sorting by; the details panel shows them all.
- **Filters** for store, installed only, and hiding unrated games.
- **Videos:** Steam trailers and YouTube gameplay videos play right in the details pane.
- **Launch** installed games straight from the list, through Steam, so a game keeps running when you close this app.
- **Self-contained:** the app runs in its own window and brings its own Python, so it doesn't need a browser or the system Python. Everything, including your cache, lives in this folder.

## Set up (once)

```bash
./setup.sh
```

This downloads two runtimes into `runtime/` (about 340 MB on disk, 14 seconds on a good connection) and checks both against their publishers' SHA-256 checksums:

- **Electron 45.2.3:** gives the app its own window. Heroic uses the same technology.
- **Standalone Python 3.13.18:** from python-build-standalone, trimmed to what the app needs.

The versions are pinned at the top of `setup.sh`. Re-run the script after changing them.

## Single-file app (AppImage)

```bash
./build-appimage.sh      # creates dist/HeroicRatings-x86_64.AppImage (about 140 MB)
```

The AppImage is one executable file that contains everything: the UI, the backend, Electron and Python. Copy it anywhere, make sure it's executable, and run it, or add it to Steam as a Non-Steam Game. Because the AppImage itself is read-only, its data (rating cache and overrides) is stored in `~/.local/share/heroic-ratings`. To carry over the cache from this folder, so you don't have to wait for a new Steam sync:

```bash
mkdir -p ~/.local/share/heroic-ratings && cp data/*.json ~/.local/share/heroic-ratings/
```

## Run it (Desktop Mode)

```bash
./launch.sh              # fullscreen
./launch.sh --windowed   # normal window: F11 toggles fullscreen, Ctrl+Q quits
```

If `runtime/` is missing, `launch.sh` falls back to the old behaviour: the system Python plus a full-screen browser window (Chromium, Chrome, Brave, Edge or Firefox).

The first run matches every title on Steam. That takes about 3–5 minutes for 500 games, and ratings appear as they arrive. Results are cached in `data/cache.json`. Later starts are instant, and scores refresh weekly.

## Add it to Game Mode

1. In Desktop Mode, open Steam → **Games → Add a Non-Steam Game** → **Browse**, and pick `launch.sh` from this folder. (Set the file filter to "All files".)
2. In the shortcut's Properties, rename it "Heroic Ratings". You can also add artwork.
3. Launch it from Game Mode. The default controller layout ("Gamepad") works as is.

Run `./setup.sh` before adding the shortcut. When you choose **Quit** in the app's menu, it closes completely and Steam returns to the library.

## Controls

| Button | Action |
| --- | --- |
| D-pad / left stick ▲ ▼ | Move (a fresh press at the top or bottom wraps round to the other end) |
| D-pad / left stick ◀ ▶ | Previous / next video |
| A | Launch the selected game, or install it with Heroic if it isn't installed (asks first) |
| B | Open the genre picker |
| X | Switch sort mode |
| View (two squares) | Reverse the sort order |
| Y | Search (on a Steam Deck with the app's on-screen keyboard) |
| LB / RB | Previous / next genre |
| LT / RT | Page up / down; **hold** for half a second to jump to the top / bottom |
| Right stick | Up/down: scroll the details pane · ◀ ▶: previous/next video |
| Right stick click (R3) | Trailer sound on/off |
| Menu (three lines) | Store filter, installed only, hide unrated, play style, perspective, Steam match, match unmatched games, settings, uninstall, refresh, quit |

**Desktop Mode:** Steam's Desktop controller layout turns the buttons into keys, and the app maps them: A = Enter, View = Tab, Y = Space, LB / RB = Ctrl / Alt. But B and Menu both send Escape (the app treats it as Menu), and X opens Steam's on-screen keyboard, which takes focus away from the app. For full controller support, **hold the Menu (three lines) button for a second** to switch Steam to its Gamepad layout, or launch the app through Steam as a Non-Steam Game. On a Deck in Desktop Mode, the app shows a warning bar about this until it sees a real gamepad button. 

To search, press **Y** (or Menu → Search…). On a Steam Deck this opens the app's own on-screen keyboard, because Steam's keyboard (Steam + X) is unreliable for non-Steam apps: D-pad to move, **A** types, **X** deletes, **Y** adds a space, **LB** toggles capitals, **Menu** finishes, **B** cancels. The results update as you type. The settings fields use the same keyboard; a physical keyboard also types into it. On a keyboard, use the arrow keys, Enter (A), Backspace (B), Esc or `m` (Menu), Tab or `r` (reverse), `x`, `y` (search), `g` or Backspace (genres), `[` `]`, `v` (trailer sound), and `/` for search. Mouse and touch also work.

## IGDB (optional, with a Twitch key)

With a free **Twitch developer key**, the app also uses **IGDB** to fill the gaps Steam leaves. Steam stays the main source wherever it has data.

- **Better matching:** games Steam's search misses are looked up on IGDB, including its alternative names; when IGDB knows the game's Steam page, the game gets its full Steam reviews (on one library this linked 9 more games, e.g. *Trine 4: Definitive Edition* → *Trine 4: The Nightmare Prince*).
- **Critic score:** the Metascore where Steam has one. Otherwise IGDB's critic score, unless it's based on fewer than 3 reviews or Wikidata's OpenCritic score is based on more reviews, in which case OpenCritic's is used. **Sort → Critic score** uses it, and the source and review count are shown (e.g. "OpenCritic · 33 reviews").
- **Scores for unrated games:** the same critic score choice, or failing that IGDB's user rating. IGDB scores based on fewer than 3 critic reviews or 5 user ratings aren't used; they still show (with their counts) in the details panel.
- **How long to beat:** main story, + extras and 100% times beside the score tiles in the details pane. IGDB has times for roughly 40% of games.
- **Play style and perspective filters** (Menu): single player, co-op, split screen, multiplayer…; first person, third person, isometric, side view…
- **In the details panel:** IGDB's user or critic score in the score tiles when it's the one in use (with its count), play styles, multiplayer details (e.g. "Local co-op up to 4"), **same series in your library** and **more like this in your library** (tap a name to jump to it).
- **Games not on Steam** get IGDB's cover, description, release year, developer and genres.

Create an application at [dev.twitch.tv/console](https://dev.twitch.tv/console) (OAuth redirect URL: `http://localhost`, client type: Confidential), then enter its client ID and secret in **Menu → Settings…** (on a Steam Deck the app's on-screen keyboard opens). The app checks them with Twitch before saving them, readable only by you, in `twitch.json` in the data folder (`~/.local/share/heroic-ratings/` for the AppImage). You can also create that file yourself:

```json
{ "client_id": "…", "client_secret": "…" }
```

or set `HEROIC_RATINGS_TWITCH_CLIENT_ID` and `HEROIC_RATINGS_TWITCH_CLIENT_SECRET`. IGDB data is fetched on the next sync (restart the app, or choose **Menu → Refresh**) and takes seconds; it's refreshed monthly.

## Layout

The header shows the number of games and, on a Steam Deck (or any machine with a battery), the battery level: ⚡ while charging, red at 20% or below. The list shows each game's title, an Installed / download badge and the value it's sorted by. The details pane on the right shows:

- **The game's trailer, playing right there** (muted and looping) once a game has been selected for a moment, with the title and four score tiles beside it. **The first tile always shows the same value as the list**, for the current sort (e.g. the weighted rating 86 with Sort: Rating, the Steam percentage 88% with Sort: % Positive). Each kind of score appears once, from the source in use, with its review count: the user score is Steam's percentage (or IGDB's user rating for games without Steam reviews), the critic score is the one the app chose (Metascore, IGDB or OpenCritic). Under the tiles, with IGDB: how long to beat (main story, + extras, 100%).
- Below: the description, where the Steam match came from (only when it was found indirectly or by another name), the game's other videos, play styles and multiplayer details, same series and similar games from your library (tap to jump), and genres. The right stick scrolls the pane.

## Videos

- **R3** (right stick click) or `v`: trailer sound on/off (remembered).
- **D-pad ◀ ▶** (or right stick ◀ ▶): previous / next video. The thumbnails under the description slide along to keep the playing one centred; tap a thumbnail (or the ‹ › at the edges) to play another, or tap the video to pause it.
- The trailer pauses while a game or Heroic is in front, or when the app isn't focused.

Sources: **Steam's store videos** (trailers, no key needed) come first, main trailer first; [hls.js](https://github.com/video-dev/hls.js) 1.5.20 is bundled as a fallback player (Apache-2.0, see `web/vendor/hls.js-LICENSE`). **YouTube videos from IGDB** (with a Twitch key) follow: videos IGDB labels as gameplay, and for games not on Steam its trailers too. They play in YouTube's embedded player, so YouTube may show ads and some uploaders don't allow embedding. Videos are only looked up for the game you're looking at, and Steam's list is cached for a month.

## Launching games

Press **A** on an installed game and confirm. The app launches it **only through the game's own Steam shortcut** (Heroic adds one when it installs a game), so the game runs as its own Steam app and keeps running when you close this app. If that isn't possible, the app says why instead of launching:

- **Installed since Steam last started:** Steam only reads its shortcuts when it starts. Restart Steam (Game Mode: Steam button → Power → Restart Steam). The app also reminds you of this when an install finishes.
- **Not in Steam:** in Heroic, open the game and choose **Add to Steam** (or turn on *Add games to Steam automatically* in Heroic's settings), then restart Steam.

## Installing games

Press **A** on a game that isn't installed and confirm. The app starts Heroic hidden in the background if it isn't running, reads the same options Heroic's install dialog offers, and shows them here. Use ◀ ▶ to change a value and **A** to toggle or select. Only the options a game actually has are shown:

| Option | Notes |
| --- | --- |
| Install location | Heroic's default folder, plus any SD card or USB drive (`<card>/Games/Heroic`), with free space |
| Platform | Windows or Linux, when the game has a native Linux build. Changing it reloads the options |
| Language | Mostly GOG games. Defaults to your system language, as Heroic does |
| Wine / Proton | Windows games only. Starts on the game's current setting; saved to the game's Heroic settings before installing |
| Wine prefix | Windows games only. **Own** by default (`<Heroic prefix folder>/<game title>`, named the way Heroic names them), or Heroic's **shared** prefix. A game that already has its own prefix keeps it |
| DLC | All owned DLC is selected by default, as in Heroic |
| Optional components | Epic games with selective downloads |

The download and install size update as you change things. **Install** is blocked if the game won't fit at the chosen location. The install is then queued in Heroic's own download queue, and its progress shows on the game's row and in the details panel (percent, speed, time left). **Keep this app open until the download finishes:** Heroic runs as part of this app, so closing the app stops the download. When it finishes, the game shows as Installed, and the app reminds you to restart Steam so the game can be launched.

**Why it works this way:** Heroic 2.22.4 can't take install requests from other apps. Its *"…is not installed, do you wish to install it?"* prompt appears, but its Yes button does nothing ([Heroic issue #6053](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/issues/6053)). As a workaround, the app starts Heroic with Chromium's remote-debugging port (a free port Heroic picks, `127.0.0.1` only), and the app uses it to navigate Heroic's own window. Heroic still does all the downloading and installing.

## Uninstalling games

Choose **Menu → Uninstall "…"** with an installed game selected. You get the same choices as Heroic's own uninstall dialog, both off by default:

- **Remove Wine prefix** (Windows games): deletes the game's Wine prefix, including any save files in it that aren't backed up. Not offered when the game uses Heroic's shared prefix.
- **Erase settings and log**: forgets the game's Heroic settings and deletes its log.

Heroic does the uninstall; the game's row shows "Uninstalling…" until it's done.

## When Heroic is already open

Installing and uninstalling need the hidden Heroic that this app starts (with its debug port). If Heroic is already open some other way, the app asks you to quit it first (Desktop Mode: tray icon → Quit; Game Mode: Steam button → Heroic → Exit game). While the app's Heroic runs, any program on the device could control it through the debug port (`127.0.0.1` only), including your logged-in stores; it's gone once Heroic is started normally again.

Earlier versions of this app added a hidden **Heroic (Debug)** Steam shortcut. The app now removes it automatically (backing up `shortcuts.vdf` first).

## Wrong or missing matches

The details pane notes when a game was matched to a Steam page with a different name, or found through Wikidata or IGDB. Games that aren't sold on Steam get a critic score when Wikidata has one; that score is used for sorting by Rating. Games with no data show as "Not on Steam". Editors, betas, soundtracks and similar library entries show as "Extra / tool" and aren't looked up. Both groups are always sorted to the bottom.

Wikidata limits how fast it can be queried, so the first sync takes a little longer: about 2–3 seconds per game Steam couldn't find.

- **Fix or add a match:** select the game, then **Menu → Steam match for "…"**. Search by name (pre-filled with the title) and pick the right Steam page. Results come from Steam's store search and, with an IGDB key, from IGDB, which also finds games no longer sold on Steam (e.g. *POSTAL: Classic and Uncut* → *Postal*, app 232770); the closest names come first. For a title too generic to search for, type the **Steam app ID**, the number in the game's Steam store address (e.g. `269270` for *LOVE*). *Not on Steam* removes a match. The game's reviews are fetched straight away.
- **Re-try every unmatched game now:** **Menu → Match unmatched games (N)** looks up all games without a Steam page again (Steam search, Wikidata and, with a key, IGDB) instead of waiting for the monthly retry. Editors and matches you set yourself are left alone.
- **By hand:** the choices are stored in `overrides.json` in the data folder. The key is the game's ID (`runner:app_name`) and the value is the Steam app ID, or `null` for no match. After editing it, choose Menu → *Refresh*.

  ```json
  { "legendary:Fortnite": null, "gog:1207658924": 22330 }
  ```

## Options

```text
python3 heroic_ratings.py [--port 8787] [--heroic-dir PATH] [--kiosk] [--no-sync]
```

| Variable | Purpose |
| --- | --- |
| `HEROIC_RATINGS_DATA` | Where the cache is stored (default `./data`; the AppImage uses `~/.local/share/heroic-ratings`) |
| `HEROIC_RATINGS_BROWSER` | Browser command for `--kiosk` |
| `HEROIC_LAUNCH_CMD` | Custom launch command; `{url}` is replaced with the `heroic://launch?...` URL (installs then use the plain `heroic://launch` prompt too) |
| `HEROIC_RATINGS_TWITCH_CLIENT_ID`, `HEROIC_RATINGS_TWITCH_CLIENT_SECRET` | Twitch developer app for IGDB game lengths (or use `twitch.json`) |
| `HEROIC_RATINGS_STEAM_USERDATA` | Steam `userdata` folder to use for the shortcut (for testing) |

The app finds the Heroic folder automatically for both the Flatpak install (`~/.var/app/com.heroicgameslauncher.hgl/config/heroic`) and the native one (`~/.config/heroic`).

## Logs

The app writes three logs to `logs/` in its data folder (`~/.local/share/heroic-ratings/logs/` for the AppImage, `data/logs/` otherwise):

| File | What's in it |
| --- | --- |
| `app.log` | Start-up details, sync steps, every action (launch, install, uninstall, settings), Heroic/Steam/IGDB problems, errors from the interface, and full tracebacks for anything that crashes. Rotated at 1 MB, three old files kept. |
| `electron.log` | The app window: start-up, a window that stops responding, and any of its processes that crash or are killed (e.g. for running out of memory). |
| `health.log` | One line of JSON every 30 seconds (every 5 seconds while Heroic is downloading or uninstalling): free memory, swap used, unwritten disk data, the kernel's memory/disk/CPU pressure and the biggest processes. Each line is saved straight to disk, so after a freeze and a hard reset the last lines show what was happening. |

## Releases (GitHub Actions)

Two workflows live in `.github/workflows/`:

- **Tests** (`ci.yml`) runs on every push and pull request: all unit tests plus the interface tests in headless Chromium, with a coverage summary.
- **Release** (`release.yml`) runs when a version tag is pushed. It runs the tests, builds the AppImage (the build scripts download Electron, Python and appimagetool and verify their checksums; downloads are cached between runs), checks that the AppImage unpacks and its backend imports, and publishes a GitHub release with `HeroicRatings-<tag>-x86_64.AppImage`, its `.sha256` file and release notes generated from the commits.

```bash
git tag v1.0.0
git push origin v1.0.0
```

It can also be started by hand from the repository's **Actions** tab (*Release → Run workflow*, with a tag name); that makes a **draft** release, to check before publishing.

## Tests

```bash
python3 -m unittest discover tests
```

This runs the matching, client, sync, API and logging tests against a throwaway fixture library (no network, nothing outside a temp folder is touched). The interface tests in `tests/test_ui.py` drive the real page in headless Chromium and are skipped unless [Playwright](https://playwright.dev/python/) is installed:

```bash
python3 -m venv data/.test/venv && data/.test/venv/bin/pip install playwright coverage
PLAYWRIGHT_BROWSERS_PATH=data/.test/pw data/.test/venv/bin/playwright install chromium-headless-shell
PLAYWRIGHT_BROWSERS_PATH=data/.test/pw data/.test/venv/bin/python -m unittest discover tests
data/.test/venv/bin/python -m coverage run --include=heroic_ratings.py -m unittest discover tests && data/.test/venv/bin/python -m coverage report
```
