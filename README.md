# Heroic Ratings

Browse your Heroic Games Launcher library (Epic, GOG and Amazon games, plus sideloaded ones) by rating. Built for the Steam Deck: it works in Game Mode, with a controller, at 1280×720/800.

- **Ratings come from Steam, for free and with no API key.** Each game is matched to its Steam page. The app shows the Steam user-review score, a weighted rating (SteamDB-style, so a game with 5 reviews at 100% doesn't beat one with 500k reviews at 96%), and the Metascore.
- **Wikidata covers the gaps, also free with no API key.** Steam's search only finds games that are currently for sale. For any game it misses, the app asks Wikidata, which finds delisted Steam games (old Football Managers, Rocket League) so they still get their Steam reviews. For games not on Steam at all (Fortnite, Alan Wake 2), Wikidata supplies the Metacritic or OpenCritic critic score, shown as "Critics · OpenCritic".
- **Genre filter** uses Steam's genre tags.
- **Sort** by weighted rating, % positive, popularity, Metascore, release year or title. The list shows the value you're sorting by; the details panel shows them all.
- **Filters** for store, installed only, and hiding unrated games.
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
| D-pad / left stick | Move |
| A | Launch the selected game, or install it with Heroic if it isn't installed (asks first) |
| B | Clear search and filters |
| X | Switch sort mode |
| View (two squares) | Reverse the sort order |
| Y | Open the genre picker |
| LB / RB | Previous / next genre |
| LT / RT, ◀ / ▶ | Page up / down |
| Right stick | Scroll the details panel |
| Menu (three lines) | Store filter, installed only, hide unrated, search, refresh, fix a match, quit |

**Desktop Mode:** Steam's Desktop controller layout turns the buttons into keys, and the app maps them: A = Enter, View = Tab, Y = Space, LB / RB = Ctrl / Alt. But B and Menu both send Escape (the app treats it as Menu), and X opens Steam's on-screen keyboard, which takes focus away from the app. For full controller support, **hold the Menu (three lines) button for a second** to switch Steam to its Gamepad layout, or launch the app through Steam as a Non-Steam Game. On a Deck in Desktop Mode, the app shows a warning bar about this until it sees a real gamepad button. If a button ever does the wrong thing, **Menu → Controller test** shows exactly what the app receives.

To search, choose Menu → Search, then press **Steam + X** for the on-screen keyboard. On a keyboard, use the arrow keys, Enter (A), Backspace (B), Esc or `m` (Menu), Tab or `r` (reverse), `x`, `y`, `[` `]`, and `/` for search. Mouse and touch also work.

## The "Heroic (Debug)" Steam shortcut

On first start, the app adds a hidden non-Steam shortcut called **Heroic (Debug)** to Steam. It starts Heroic in the background, with no window (`--no-gui`), and with its remote-debugging port (`127.0.0.1` only), and the app will use it to start Heroic for installs and launches. That way Heroic, and the games it runs, belong to their own Steam entry instead of running inside this app, so closing this app doesn't close your game.

Steam only reads its shortcuts when it starts, so **restart Steam once** after the app adds it, or updates it after an app update changes the shortcut (Game Mode: Steam button → Power → Restart Steam). The app backs up `shortcuts.vdf` before changing it (`shortcuts.vdf.heroic-ratings-<date>.bak` next to it), and **Menu → Heroic (Debug) Steam shortcut** shows its status. The shortcut is under Steam's Hidden collection if you want to see it.

## Launching games

Press **A** on an installed game and confirm. The game always runs as its own Steam app, never inside this one, so closing Heroic Ratings doesn't close it:

1. **The game's own Steam shortcut** (Heroic adds one when it installs a game) is used if Steam has loaded it.
2. **Otherwise, through Heroic (Debug):** the app starts it via Steam if it isn't running yet, then hands it the launch. A shortcut Heroic added since Steam last started counts as not loaded, so newly installed games take this route until the next Steam restart.

## Installing games

Press **A** on a game that isn't installed and confirm. The app starts Heroic through the **Heroic (Debug)** shortcut if it isn't running, reads the same options Heroic's install dialog offers, and shows them here. Use ◀ ▶ to change a value and **A** to toggle or select. Only the options a game actually has are shown:

| Option | Notes |
| --- | --- |
| Install location | Heroic's default folder, plus any SD card or USB drive (`<card>/Games/Heroic`), with free space |
| Platform | Windows or Linux, when the game has a native Linux build. Changing it reloads the options |
| Language | Mostly GOG games. Defaults to your system language, as Heroic does |
| Wine / Proton | Windows games only. Starts on the game's current setting; saved to the game's Heroic settings before installing |
| Wine prefix | Windows games only. **Own** by default (`<Heroic prefix folder>/<game title>`, named the way Heroic names them), or Heroic's **shared** prefix. A game that already has its own prefix keeps it |
| DLC | All owned DLC is selected by default, as in Heroic |
| Optional components | Epic games with selective downloads |

The download and install size update as you change things. **Install** is blocked if the game won't fit at the chosen location. The install is then queued in Heroic's own download queue, and its progress shows on the game's row and in the details panel (percent, speed, time left). Because Heroic runs as its own Steam app, the download carries on if you close this app. When it finishes, the game shows as Installed.

**Why it works this way:** Heroic 2.22.4 can't take install requests from other apps. Its *"…is not installed, do you wish to install it?"* prompt appears, but its Yes button does nothing ([Heroic issue #6053](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher/issues/6053)). As a workaround, Heroic (Debug) starts Heroic with Chromium's remote-debugging port (a free port Heroic picks, `127.0.0.1` only), and the app uses it to navigate Heroic's own window. Heroic still does all the downloading and installing.

## Uninstalling games

Choose **Menu → Uninstall "…"** with an installed game selected. You get the same choices as Heroic's own uninstall dialog, both off by default:

- **Remove Wine prefix** (Windows games): deletes the game's Wine prefix, including any save files in it that aren't backed up. Not offered when the game uses Heroic's shared prefix.
- **Erase settings and log**: forgets the game's Heroic settings and deletes its log.

Heroic does the uninstall; the game's row shows "Uninstalling…" until it's done.

## When Heroic is already open

Launches and installs need Heroic to be running from **Heroic (Debug)**. If Heroic is open some other way (your normal Heroic shortcut, or the desktop app), the app asks you to quit it first (Desktop Mode: tray icon → Quit; Game Mode: Steam button → Heroic → Exit game). While Heroic runs from Heroic (Debug), any program on the device could control it through the debug port, including your logged-in stores; the port is gone once Heroic is started normally again.

## Wrong or missing matches

The details panel shows which Steam game each title was matched to, and notes when the match was found through Wikidata. Games that aren't sold on Steam get a critic score when Wikidata has one; that score is used for sorting by Rating. Games with no data show as "Not on Steam". Editors, betas, soundtracks and similar library entries show as "Extra / tool" and aren't looked up. Both groups are always sorted to the bottom.

Wikidata limits how fast it can be queried, so the first sync takes a little longer: about 2–3 seconds per game Steam couldn't find.

- **Remove a bad match:** Menu → *Wrong Steam match for "…"*.
- **Set the right match:** edit `data/overrides.json`. The key is the game's ID (`runner:app_name`) and the value is the Steam app ID, or `null` for no match. Then choose Menu → *Refresh*.

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
| `HEROIC_RATINGS_STEAM_USERDATA` | Steam `userdata` folder to use for the shortcut (for testing) |

The app finds the Heroic folder automatically for both the Flatpak install (`~/.var/app/com.heroicgameslauncher.hgl/config/heroic`) and the native one (`~/.config/heroic`).

## Tests

```bash
python3 -m unittest discover tests
```
