#!/usr/bin/env bash
# Starts Heroic Ratings in its own fullscreen window. Add this script to Steam as a
# Non-Steam Game to use it from Game Mode.
#
#   ./launch.sh              fullscreen (Game Mode / Deck)
#   ./launch.sh --windowed   normal window (F11 toggles fullscreen, Ctrl+Q quits)
cd "$(dirname "$(readlink -f "$0")")" || exit 1

if [ -x runtime/electron/electron ]; then
  # --no-sandbox: Chromium's setuid sandbox helper can't be installed without root.
  # The window only ever loads this app's own local page (navigation is blocked).
  exec runtime/electron/electron --no-sandbox electron "$@"
fi

echo "Bundled runtime not found - run ./setup.sh once. Falling back to a system browser." >&2
PY=python3
[ -x runtime/python/bin/python3 ] && PY=runtime/python/bin/python3
exec "$PY" heroic_ratings.py --kiosk "$@"
