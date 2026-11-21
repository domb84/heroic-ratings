#!/usr/bin/env bash
# Builds a single-file app: dist/HeroicRatings-x86_64.AppImage
# It contains the UI, the backend, Electron and Python. Copy it anywhere and run it,
# or add it to Steam as a Non-Steam Game. Your data (rating cache, overrides) is
# stored in ~/.local/share/heroic-ratings, outside the AppImage.
set -euo pipefail

APPIMAGETOOL_VERSION="1.9.1"
# appimagetool publishes no checksum file; this is the SHA-256 of the 1.9.1 x86_64 release asset.
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"

cd "$(dirname "$(readlink -f "$0")")"
ROOT="$PWD"
[ "$(uname -m)" = "x86_64" ] || { echo "AppImage build is set up for x86_64 only"; exit 1; }

./setup.sh                                   # make sure runtime/ is present and current

BUILD="$ROOT/build"
APPDIR="$BUILD/AppDir"
rm -rf "$APPDIR"; mkdir -p "$APPDIR" "$ROOT/dist"

# ---- tool
TOOL="$BUILD/appimagetool-$APPIMAGETOOL_VERSION"
if [ ! -s "$TOOL" ]; then
  echo "Downloading appimagetool $APPIMAGETOOL_VERSION…"
  curl -fL --retry 3 --progress-bar -o "$TOOL.part" \
    "https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/appimagetool-x86_64.AppImage"
  mv "$TOOL.part" "$TOOL"
fi
echo "$APPIMAGETOOL_SHA256  $TOOL" | sha256sum -c --quiet - || { echo "appimagetool checksum mismatch"; rm -f "$TOOL"; exit 1; }
chmod +x "$TOOL"

# ---- app files
cp -a heroic_ratings.py web electron runtime "$APPDIR/"
rm -rf "$APPDIR/runtime/.downloads"
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +
[ -s web/icon.png ] || python3 tools/make_icon.py web/icon.png
cp web/icon.png "$APPDIR/web/icon.png"
cp web/icon.png "$APPDIR/heroic-ratings.png"
ln -s heroic-ratings.png "$APPDIR/.DirIcon"

cat > "$APPDIR/heroic-ratings.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Heroic Ratings
Comment=Browse your Heroic library by rating
Exec=AppRun
Icon=heroic-ratings
Categories=Game;
Terminal=false
EOF

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# Entry point of the AppImage. The image itself is read-only, so data goes to XDG dirs.
HERE="$(dirname "$(readlink -f "$0")")"
export HEROIC_RATINGS_DATA="${HEROIC_RATINGS_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/heroic-ratings}"
mkdir -p "$HEROIC_RATINGS_DATA"
unset ELECTRON_RUN_AS_NODE
# --no-sandbox: Chromium's setuid sandbox helper can't work from inside an AppImage.
# The window only ever loads this app's own local page (navigation is blocked).
exec "$HERE/runtime/electron/electron" --no-sandbox "$HERE/electron" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# ---- pack
OUT="$ROOT/dist/HeroicRatings-x86_64.AppImage"
rm -f "$OUT"
# Extract-and-run lets this work where FUSE isn't available (containers, sandboxes).
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$TOOL" --comp zstd --no-appstream "$APPDIR" "$OUT"
chmod +x "$OUT"
echo
echo "Built: $OUT ($(du -h "$OUT" | cut -f1))"
