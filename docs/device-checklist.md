# Device checklist (before a release)

Things the automated tests can't cover: they need the Steam Deck, Steam, the real Heroic and
a real controller. Run them in **Game Mode** with the AppImage being released, launched from
its Steam shortcut. If something fails, note the time; the app's logs are in
`~/.local/share/heroic-ratings/logs/` (`app.log`, `electron.log`, `health.log`).

## Start-up
- [ ] The app opens fullscreen, the list fills in, and the battery level shows top right.
- [ ] **Menu** is grouped (Selected game, Filters, Ratings, App) and fits without scrolling; the cursor skips the headings. **Menu → Settings…** shows *Check for updates: On*; **Menu → About…** shows the version (e.g. *v1.1.0*) and the Heroic version. With a Twitch key, **Test IGDB connection** says *IGDB is working*.
- [ ] About → Heroic shows *2.22.4* (any warning after it means the installed Heroic isn't fully supported).
- [ ] Updating (after v1.1.0 is published): build an older-looking copy with `HEROIC_RATINGS_VERSION=v1.0.9 ./build-appimage.sh`, copy `dist/HeroicRatings-x86_64.AppImage` somewhere and run it. The header shows *Update v1.1.0 available*; **Menu → Update to v1.1.0** shows the percentage in the header, then asks you to quit. Start it again: Settings shows v1.1.0.
- [ ] `app.log` starts with `starting Heroic Ratings v1.1.0 …` and contains no `refused` lines.

## Controller
- [ ] D-pad ▲ ▼ moves; a fresh ▲ on the first game jumps to the last (and ▼ back to the first); holding ▲ ▼ stops at the ends.
- [ ] Tap LT / RT pages; **hold** LT / RT for half a second jumps to the top / bottom.
- [ ] **Y** opens the on-screen keyboard; typing filters the list as you go; **Menu** finishes, **B** cancels.
- [ ] **B** opens the genre picker; **X** changes the sort and the first tile always matches the list.
- [ ] D-pad ◀ ▶ switches the trailer; **R3** turns its sound on and off, and you hear it.
- [ ] A **YouTube** trailer (pick a game that isn't on Steam, e.g. *Fortnite*) plays inside the app.

## Installing and uninstalling (the queue)
Close Heroic completely first.
- [ ] Install a small game: the options screen appears **on the first try** (no "couldn't get the install options").
- [ ] While it downloads, start a second install and an uninstall: their rows show *Queued #2* / *Uninstall queued #3*.
- [ ] **Menu → Cancel queued …** removes a waiting one.
- [ ] Each job starts only after the previous one has finished; the first install shows progress, then *Installed*.
- [ ] After the install, the app asks you to restart Steam.
- [ ] **Menu → Quit** while something is running asks *Heroic is still working … Quit anyway?*; **B** keeps it running.
- [ ] Quit with jobs still queued, open the app again: it says *Resuming N queued jobs* and runs them.
- [ ] Uninstall a game: it works **on the first try**, and the row goes from *Uninstalling…* straight to not installed.

## Launching
- [ ] Restart Steam (Steam button → Power → Restart Steam), then launch the game you just installed: it starts **under its own name** in Steam, not inside Heroic Ratings.
- [ ] Close Heroic Ratings while the game runs: the game keeps running.
- [ ] Heroic didn't pop up in front of the app at any point.

## Matching
- [ ] **Menu → Steam match for "…"** on an unmatched game: search finds candidates; typing a Steam app ID (e.g. `269270`) finds *LOVE*.
- [ ] **Menu → Match unmatched games (N)** runs and the progress bar shows it.
- [ ] *Shadow Tactics: Blades of the Shogun* appears once.

## Stability
- [ ] Install a large game (or two in the queue) and keep using the app. If the Deck freezes, after the hard reset copy `~/.local/share/heroic-ratings/logs/` and run the journal command from the freeze investigation; the last lines of `health.log` show what was filling up.
