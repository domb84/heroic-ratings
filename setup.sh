#!/usr/bin/env bash
# Downloads the bundled runtimes into ./runtime so the app needs nothing from the system:
#   runtime/electron  - Electron (the app's own window; same tech Heroic uses)
#   runtime/python    - standalone CPython (python-build-standalone)
# Both downloads are checked against the publishers' SHA-256 checksums.
#
#   ./setup.sh             install / update runtimes
#   (to make a single-file app, run ./build-appimage.sh)
set -euo pipefail

ELECTRON_VERSION="45.2.3"
PYTHON_VERSION="3.13.18"
PYTHON_BUILD="20261026"

cd "$(dirname "$(readlink -f "$0")")"
ROOT="$PWD"
RT="$ROOT/runtime"
DL="$RT/.downloads"
mkdir -p "$DL"

case "$(uname -m)" in
  x86_64) E_ARCH=x64; P_ARCH=x86_64 ;;
  aarch64) E_ARCH=arm64; P_ARCH=aarch64 ;;
  *) echo "Unsupported CPU: $(uname -m)"; exit 1 ;;
esac

fetch() { # url dest
  if [ ! -s "$2" ]; then
    echo "Downloading $(basename "$2")…"
    curl -fL --retry 3 --progress-bar -o "$2.part" "$1"
    mv "$2.part" "$2"
  fi
}

verify() { # file sumsfile
  local name expected actual
  name="$(basename "$1")"
  expected="$(grep -E "[ *]${name//+/\\+}\$" "$2" | awk '{print $1}' | head -n1)"
  actual="$(sha256sum "$1" | awk '{print $1}')"
  if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
    echo "Checksum mismatch for $name (expected '${expected:-none}', got $actual)"; rm -f "$1"; exit 1
  fi
  echo "  checksum OK: $name"
}

# ---------------------------------------------------------------- Electron
E_ZIP="electron-v${ELECTRON_VERSION}-linux-${E_ARCH}.zip"
E_BASE="https://github.com/electron/electron/releases/download/v${ELECTRON_VERSION}"
if [ "$(cat "$RT/electron/.version" 2>/dev/null)" != "$ELECTRON_VERSION" ]; then
  fetch "$E_BASE/$E_ZIP" "$DL/$E_ZIP"
  fetch "$E_BASE/SHASUMS256.txt" "$DL/electron-SHASUMS256-$ELECTRON_VERSION.txt"
  verify "$DL/$E_ZIP" "$DL/electron-SHASUMS256-$ELECTRON_VERSION.txt"
  rm -rf "$RT/electron"; mkdir -p "$RT/electron"
  python3 -c 'import sys,zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$DL/$E_ZIP" "$RT/electron" 2>/dev/null \
    || unzip -q "$DL/$E_ZIP" -d "$RT/electron"
  chmod +x "$RT/electron/electron" "$RT/electron/chrome_crashpad_handler" 2>/dev/null || true
  # Keep only the English UI strings (saves ~40 MB).
  find "$RT/electron/locales" -name '*.pak' ! -name 'en-US.pak' -delete
  echo "$ELECTRON_VERSION" > "$RT/electron/.version"
fi

# ---------------------------------------------------------------- Python
P_TGZ="cpython-${PYTHON_VERSION}+${PYTHON_BUILD}-${P_ARCH}-unknown-linux-gnu-install_only_stripped.tar.gz"
P_BASE="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD}"
if [ "$(cat "$RT/python/.version" 2>/dev/null)" != "$PYTHON_VERSION+$PYTHON_BUILD" ]; then
  fetch "$P_BASE/${P_TGZ//+/%2B}" "$DL/$P_TGZ"
  fetch "$P_BASE/SHA256SUMS" "$DL/python-SHA256SUMS-$PYTHON_BUILD"
  verify "$DL/$P_TGZ" "$DL/python-SHA256SUMS-$PYTHON_BUILD"
  rm -rf "$RT/python"
  tar -xzf "$DL/$P_TGZ" -C "$RT"      # extracts to runtime/python
  # Drop parts the app never uses (test suite, Tk GUI, pip, headers): ~260 MB -> ~80 MB.
  PL="$RT/python/lib/python${PYTHON_VERSION%.*}"
  rm -rf "$PL"/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,pydoc_data} "$PL/site-packages/pip"* \
         "$PL"/config-* "$RT/python/include" "$RT/python/share" "$RT/python"/lib/{tcl,tk,itcl,thread}* \
         "$RT/python"/lib/lib{tcl,tk}* "$PL"/lib-dynload/_tkinter* "$PL"/lib-dynload/_test* "$PL"/lib-dynload/xx*
  find "$RT/python" -name __pycache__ -type d -prune -exec rm -rf {} +
  echo "$PYTHON_VERSION+$PYTHON_BUILD" > "$RT/python/.version"
fi

"$RT/python/bin/python3" -c 'import ssl, sys; print("  bundled Python", sys.version.split()[0], "OK")'
echo "  bundled Electron $(cat "$RT/electron/.version") OK"
rm -rf "$DL"
echo "Runtimes ready. Start the app with ./launch.sh"
