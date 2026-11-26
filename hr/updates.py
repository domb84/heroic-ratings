"""Updates: asks GitHub for the latest release (at most once a day) and, when the app runs
as an AppImage, can download it, check it against the release's .sha256 and swap it in.

The new AppImage replaces the file in place (same path, so the Steam shortcut keeps
working); the running app carries on from the old one until it's restarted."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

from . import config as cfg
from . import util
from .config import APP_VERSION, UA
from .logs import log, log_error

REPO = "domb84/heroic-ratings"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"   # the only place updates come from
MAX_APPIMAGE = 1024 ** 3
CHECK_SECS = 24 * 3600
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(v: str | None) -> tuple[int, int, int] | None:
    """'v1.2.3', '1.2' or a build of one ('v1.0.0-4-gabc123-dirty') -> (1, 2, 3).
    None for builds without a release tag ('dev', a bare commit hash)."""
    m = _VERSION_RE.match((v or "").strip())
    return (int(m[1]), int(m[2]), int(m[3] or 0)) if m else None


def fetch_latest(timeout: float = 10) -> dict:
    req = urllib.request.Request(LATEST_URL, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    out = {"latest": str(d.get("tag_name") or ""), "url": str(d.get("html_url") or RELEASES_PAGE)}
    for a in d.get("assets") or []:
        name, url = str(a.get("name") or ""), str(a.get("browser_download_url") or "")
        if name.endswith("-x86_64.AppImage"):
            out.update(appimage=url, size=a.get("size"))
        elif name.endswith("-x86_64.AppImage.sha256"):
            out["sha256_url"] = url
    return out


def _open(url: str, timeout: float = 30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)


class Updates:
    """Remembers the last check (and whether checking is on) in <data>/updates.json."""
    def __init__(self, path: Path | None = None, current: str = APP_VERSION, fetch=fetch_latest, clock=time.time,
                 appimage: str | None = None, opener=_open):
        self.path = path or cfg.DATA_DIR / "updates.json"
        self.current, self.fetch, self.clock = current, fetch, clock
        # the AppImage this app runs from (its runtime sets $APPIMAGE); None: running from source
        where = appimage or os.environ.get("APPIMAGE")
        self.appimage = Path(where) if where else None
        self.opener = opener
        self.install_state: dict = {}       # {"phase": "downloading" | "done" | "error", ...}
        self.lock = threading.Lock()
        saved = util.read_json(self.path, {})
        self.data = saved if isinstance(saved, dict) else {}

    @property
    def enabled(self) -> bool:
        return self.data.get("enabled", True) is not False

    def set_enabled(self, on: bool):
        with self.lock:
            self.data["enabled"] = bool(on)
            if on:
                self.data.pop("checked", None)      # check straight away
            self._save()
        log("update checks", "on" if on else "off")

    def _save(self):
        try:
            util.write_json_atomic(self.path, self.data)
        except OSError as e:
            log_error("couldn't save update settings:", exc=e)

    def check(self, force: bool = False) -> None:
        """Ask GitHub, unless checking is off or the last check was under a day ago."""
        if not self.enabled or parse_version(self.current) is None:
            return
        now = self.clock()
        if not force and now - float(self.data.get("checked") or 0) < CHECK_SECS:
            return
        try:
            r = self.fetch()
        except Exception as e:   # offline, rate limited, no releases yet: try again tomorrow
            log("update check failed:", repr(e))
            r = {}
        with self.lock:
            self.data["checked"] = now
            if r.get("latest"):
                for k in ("appimage", "sha256_url", "size"):
                    self.data.pop(k, None)
                self.data.update(r)
            self._save()
        if self.available():
            log("update available:", self.data["latest"], "(running", self.current + ")")

    def available(self) -> dict | None:
        """{"version", "url"} when a newer release than this build is out."""
        if not self.enabled:
            return None
        cur, latest = parse_version(self.current), parse_version(self.data.get("latest"))
        if not cur or not latest or latest <= cur:
            return None
        return {"version": self.data["latest"], "url": self.data.get("url") or RELEASES_PAGE,
                "size": self.data.get("size"), "can_install": self.can_install() is None,
                "install": dict(self.install_state)}

    # -- installing
    def can_install(self) -> str | None:
        """None if the update can be installed from here, else why not."""
        if not self.appimage:
            return "not-appimage"
        if not self.appimage.is_file() or not os.access(self.appimage.parent, os.W_OK):
            return "not-writable"
        url, sums = self.data.get("appimage") or "", self.data.get("sha256_url") or ""
        if not (url.startswith(DOWNLOAD_PREFIX) and sums.startswith(DOWNLOAD_PREFIX)):
            return "no-download"
        return None

    def start_install(self) -> dict:
        why = self.can_install() if self.available() else "no-update"
        if why:
            return {"ok": False, "error": why}
        with self.lock:
            if self.install_state.get("phase") == "downloading":
                return {"ok": True}
            self.install_state = {"phase": "downloading", "done": 0, "total": self.data.get("size") or 0}
        threading.Thread(target=self.install, name="update", daemon=True).start()
        return {"ok": True}

    def install(self):
        """Download the new AppImage next to this one, check it, and swap it in."""
        target = self.appimage
        part = target.with_name("." + target.name + ".update")
        version = self.data["latest"]
        log("downloading update", version, "->", target)
        try:
            with self.opener(self.data["sha256_url"]) as r:
                expected = r.read(4096).decode(errors="replace").split()[0].lower()
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("the release's .sha256 file isn't a checksum")
            h = hashlib.sha256()
            with self.opener(self.data["appimage"], 60) as r, open(part, "wb") as out:
                total = int(r.headers.get("Content-Length") or 0) or self.data.get("size") or 0
                done = 0
                while chunk := r.read(1 << 20):
                    done += len(chunk)
                    if done > MAX_APPIMAGE:
                        raise ValueError("download is far bigger than an AppImage")
                    h.update(chunk)
                    out.write(chunk)
                    self.install_state.update(done=done, total=total)
                out.flush()
                os.fsync(out.fileno())
            if h.hexdigest() != expected:
                raise ValueError("the download doesn't match the release's checksum")
            with open(part, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    raise ValueError("the download isn't an AppImage")
            os.chmod(part, 0o755)
            os.replace(part, target)        # atomic: the old file stays whole until this moment
            log("update installed:", version)
            self.install_state = {"phase": "done", "version": version}
        except Exception as e:   # network, disk full, bad file: keep the current AppImage
            log_error("update failed:", repr(e))
            self.install_state = {"phase": "error", "error": str(e)}
            try:
                part.unlink()
            except OSError:
                pass

    def view(self) -> dict:
        return {"enabled": self.enabled, "available": self.available(), "page": RELEASES_PAGE,
                "checkable": parse_version(self.current) is not None, "install": dict(self.install_state)}

    def worker(self, every: float = 3600):
        while True:
            try:
                self.check()
            except Exception as e:   # never let the thread die
                log_error("update check crashed:", exc=e)
            time.sleep(every)

    def start(self):
        threading.Thread(target=self.worker, name="updates", daemon=True).start()
        return self
