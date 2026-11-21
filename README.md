# Heroic Ratings

Browse your Heroic Games Launcher library (Epic, GOG and Amazon games, plus sideloaded ones) by rating. Built for the Steam Deck: it works in Game Mode, with a controller, at 1280×720/800.

- **Ratings come from Steam, for free and with no API key.** Each game is matched to its Steam page. The app shows the Steam user-review score, a weighted rating (SteamDB-style, so a game with 5 reviews at 100% doesn't beat one with 500k reviews at 96%), and the Metascore.
- **Genre filter** uses Steam's genre tags.
- **Sort** by weighted rating, % positive, popularity, Metascore, release year or title.
- **Filters** for store, installed only, and hiding unrated games.
- **Launch** installed games straight from the list. The app hands them to Heroic.
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

The Deck's own controller reports the View and Menu buttons the other way round, so the app swaps them automatically for Valve controllers. If they're still reversed, change **Menu → Swap Menu / View buttons** (Auto / On / Off).

To search, choose Menu → Search, then press **Steam + X** for the on-screen keyboard. On a keyboard, use the arrow keys, Enter, Esc, `x`, `y`, `[` `]`, `r`, `m`, and `/` for search. Mouse and touch also work.

## Installing games

Press **A** on a game that isn't installed and confirm. Heroic opens and asks *"…is not installed, do you wish to install it?"*. That box is a desktop pop-up, so the controller's buttons won't work in it. Tap **Yes** on the touchscreen, or use the right trackpad as a mouse. Then pick the install options in Heroic's own dialog. When the download finishes, the game shows as Installed here within a few seconds.

**In Game Mode, if Heroic wasn't already running, it runs as part of this app.** Closing Heroic Ratings with *Exit game* would then also stop the download. Leave the app open until the download finishes, or start Heroic separately first.

## Wrong or missing matches

The details panel shows which Steam game each title was matched to. Games that aren't sold on Steam can't get a rating; examples are Epic exclusives like Fortnite and Alan Wake 2, and GOG-only releases. They show as "Not on Steam" and are always sorted to the bottom.

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
| `HEROIC_LAUNCH_CMD` | Custom launch command; `{url}` is replaced with the `heroic://launch?...` URL |

The app finds the Heroic folder automatically for both the Flatpak install (`~/.var/app/com.heroicgameslauncher.hgl/config/heroic`) and the native one (`~/.config/heroic`).

## Tests

```bash
python3 -m unittest discover tests
```
