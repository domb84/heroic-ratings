"""Which Heroic is installed, and which adapter talks to it.

The version comes from package.json inside Heroic's app.asar, so it's known without
starting Heroic. HEROIC_RATINGS_HEROIC_VERSION overrides it (tests, trying things out).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
from pathlib import Path

from ..config import HEROIC_FLATPAK_ID, HOME
from .base import HeroicAdapter
from .v2 import Heroic2
from .v3 import Heroic3

# newest first; the first one whose majors include the installed version is used
ADAPTERS: list[type[HeroicAdapter]] = [Heroic3, Heroic2]


def asar_candidates() -> list[Path]:
    out = []
    for root in (HOME / ".local/share/flatpak", Path("/var/lib/flatpak")):     # Flatpak: user, then system
        out.append(root / "app" / HEROIC_FLATPAK_ID / "current/active/files/bin/heroic/resources/app.asar")
    exe = shutil.which("heroic")
    if exe:
        out.append(Path(os.path.realpath(exe)).parent / "resources/app.asar")
    out += [Path("/opt/Heroic/resources/app.asar"), Path("/usr/lib/heroic/resources/app.asar"),
            Path("/usr/lib/heroic-games-launcher-bin/resources/app.asar")]
    return out


def read_asar_version(path: Path) -> str | None:
    """"version" from package.json at the top of an Electron app.asar."""
    try:
        with open(path, "rb") as f:
            _, header_size, _, json_len = struct.unpack("<4I", f.read(16))
            if json_len > 50_000_000:
                return None
            header = json.loads(f.read(json_len))
            entry = header["files"]["package.json"]
            f.seek(8 + header_size + int(entry["offset"]))
            pkg = json.loads(f.read(min(int(entry["size"]), 1_000_000)))
        v = pkg.get("version")
        return str(v) if v else None
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        return None


def detect_version(candidates: list[Path] | None = None) -> str | None:
    forced = os.environ.get("HEROIC_RATINGS_HEROIC_VERSION")
    if forced:
        return forced
    for p in candidates if candidates is not None else asar_candidates():
        v = read_asar_version(p)
        if v:
            return v
    return None


def parse(v: str | None) -> tuple[int, ...] | None:
    """"2.22.4" -> (2, 22, 4); "3.0.0-beta.1" -> (3, 0, 0)."""
    m = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def pick(version: str | None) -> tuple[HeroicAdapter, str]:
    """(adapter, status). status:
      "ok"          written for this version
      "preliminary" written for this version from Heroic's source, not yet tried on a device
      "untested"    same major version, newer than the adapter was checked against
      "unknown"     Heroic's version couldn't be read: assume the stable (non-preliminary) adapter
      "unsupported" no adapter for this major version (the newest is used to read the
                    library; installing and uninstalling are off)"""
    v = parse(version)
    if v is None:          # can't tell: the adapter for the Heroic most people have (the stable one)
        return next(c for c in ADAPTERS if not c.preliminary)(), "unknown"
    for cls in ADAPTERS:
        if v[0] in cls.majors:
            if v > parse(cls.tested):
                return cls(), "untested"
            return cls(), "preliminary" if cls.preliminary else "ok"
    # unknown major version: the newest adapter for a newer Heroic, the oldest for an older one
    return (ADAPTERS[0]() if v[0] > max(ADAPTERS[0].majors) else ADAPTERS[-1]()), "unsupported"
