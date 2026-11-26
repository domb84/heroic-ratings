# Third-party software and data

Heroic Ratings itself is under the MIT licence (see `LICENSE`). The AppImage also
bundles the software below, each under its own licence. The licence texts are included
in the AppImage at the paths given.

| Component | Licence | Licence text |
| --- | --- | --- |
| [Electron](https://www.electronjs.org/) 45.2.3 | MIT | `runtime/electron/LICENSE` |
| Chromium (part of Electron) and its components | BSD-3-Clause and others | `runtime/electron/LICENSES.chromium.html` |
| [Python](https://www.python.org/) 3.13.18, via [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | PSF License (and the licences of the libraries it bundles, listed in that file) | `runtime/python/lib/python3.13/LICENSE.txt` |
| [hls.js](https://github.com/video-dev/hls.js) 1.5.20 | Apache-2.0 | `web/vendor/hls.js-LICENSE` |

The AppImage is packed with [appimagetool](https://github.com/AppImage/appimagetool) (MIT), which isn't part of the result.

## Data sources

The app shows data fetched at run time from these services; it isn't bundled:

- **Steam** (Valve): store search, reviews, tags, store videos, images. Steam and the Steam logo are trademarks of Valve Corporation.
- **Wikidata**: game identifiers and critic scores, under [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
- **IGDB.com** (Twitch), when you add your own Twitch developer key: critic and user scores, how long to beat, play styles, series and similar games, covers and YouTube video links. Data from [IGDB.com](https://www.igdb.com/), used under the Twitch Developer Services Agreement.
- **YouTube**: videos linked from IGDB play in YouTube's embedded player, under YouTube's terms.
- **Heroic Games Launcher**: your library is read from Heroic's files; installs, uninstalls and launches are done by Heroic itself.

Heroic Ratings isn't affiliated with Valve, Epic Games, GOG, Amazon, Twitch/IGDB, Wikimedia, YouTube or the Heroic Games Launcher project.
