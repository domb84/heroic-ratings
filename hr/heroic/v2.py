"""Heroic 2.x (written against 2.22.4): its library files, launch URLs and window API."""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from .. import util
from .base import HeroicAdapter


# Heroic's window exists before Heroic has loaded its library and store logins; until
# then getGameInfo / getInstallInfo come back empty. Ask again until they answer.
_JS_UNTIL = r"""
const until = async (fn, secs) => {
  for (let i = 0; i < secs * 2; i++) {
    try { const v = await fn(); if (v) return v; } catch (e) {}
    await new Promise(r => setTimeout(r, 500));
  }
  return null;
};
for (let i = 0; i < 60 && !window.api; i++) await new Promise(r => setTimeout(r, 500));
// Heroic's own per-game prefix: <defaultWinePrefixDir>/<title without special characters>
// (same character list as Heroic's removeSpecialcharacters)
const HEROIC_SPECIAL = new RegExp("[:|/|*|?|<|>|\\\\|&|{|}|%|$|@|`|!|™|+|'|\"|®]", "gi");
const ownPrefix = (app, gs, title) => {
  if (gs && gs.winePrefix && gs.winePrefix !== app.winePrefix) return gs.winePrefix;   // already has its own
  return app.defaultWinePrefixDir + "/" + title.replaceAll(HEROIC_SPECIAL, "");
};
"""

# Everything Heroic's own install dialog would offer. Values and defaults follow
# Heroic 2.22.4's DownloadDialog / WineSelector.
_INSTALL_OPTIONS_JS = r"""
(async (appName, runner, platform) => {
  %s
  const api = window.api;
  if (!api) return { error: 'not-ready' };
  const gi = await until(() => api.getGameInfo(appName, runner), 30);
  if (!gi) return { error: 'unknown-game' };
  if (gi.is_installed) return { error: 'installed' };
  const platforms = ['Windows'];
  if (gi.is_linux_native) platforms.push('linux');
  if (!platforms.includes(platform)) platform = 'Windows';
  const rp = runner === 'legendary' ? platform : (platform === 'Windows' ? 'windows' : platform);
  const info = await until(async () => { const i = await api.getInstallInfo(appName, runner, rp); return i && i.manifest ? i : null; }, 30);
  if (!info) return { error: 'no-install-info' };
  const m = info.manifest;
  const app = await api.requestAppSettings();
  const out = {
    title: gi.title, platforms, platform,
    download: m.download_size || 0, disk: m.disk_size || 0, perLang: m.perLangSize || null,
    languages: (m.languages || []).slice().sort(),
    dlcs: (runner === 'legendary' || runner === 'gog') && info.game && info.game.owned_dlc
      ? info.game.owned_dlc.map(d => ({ id: d.app_name, title: d.title, perLang: d.perLangSize || null })) : [],
    sdl: [], defaultPath: app.defaultInstallPath, wine: [], wineDefault: null,
    uiLangs: navigator.languages || [navigator.language],
  };
  if (runner === 'legendary') {
    try {
      const o = await api.getGameOverride();
      if (o && o.sdl_config && o.sdl_config[appName]) out.sdl = (await api.getGameSdl(appName)) || [];
    } catch (e) {}
  }
  if (platform === 'Windows') {
    out.wine = ((await api.getAlternativeWine()) || []).map(w => ({ name: w.name, type: w.type, bin: w.bin }));
    const gs = await api.requestGameSettings(appName);
    const cur = gs && gs.wineVersion && out.wine.find(w => w.bin === gs.wineVersion.bin);
    out.wineDefault = cur ? cur.bin : (out.wine[0] ? out.wine[0].bin : null);
    out.prefixOwn = ownPrefix(app, gs, gi.title || appName);
    out.prefixShared = app.winePrefix;
  }
  return out;
})(%s, %s, %s)
"""

# Save the chosen Wine/Proton the way the dialog does, then queue the install with
# Heroic's own install function.
_INSTALL_WITH_JS = r"""
(async (p, wineBin, sharedPrefix) => {
  %s
  const api = window.api;
  if (!api) return 'not-ready';
  const gameInfo = await until(() => api.getGameInfo(p.appName, p.runner), 30);
  if (!gameInfo) return 'unknown-game';
  if (gameInfo.is_installed) return 'installed';
  const q = await api.getDMQueueInformation();   // already queued (e.g. a retried request)
  if ((q.elements || []).some(e => e.params.appName === p.appName)) return 'queued';
  if (p.platformToInstall === 'Windows') {
    // Like Heroic's install dialog: its own prefix unless "shared" was chosen, plus the Wine/Proton picked.
    const app = await api.requestAppSettings();
    const gs = await api.requestGameSettings(p.appName);
    const config = { ...gs, winePrefix: sharedPrefix ? app.winePrefix : ownPrefix(app, gs, gameInfo.title || p.appName) };
    const w = wineBin && ((await api.getAlternativeWine()) || []).find(x => x.bin === wineBin);
    if (w) config.wineVersion = w;
    await api.writeConfig({ appName: p.appName, config });
  }
  const queued = api.install({ ...p, gameInfo });
  await Promise.race([queued, new Promise(r => setTimeout(r, 5000))]);
  return 'queued';
})(%s, %s, %s)
"""

# What Heroic's uninstall dialog shows (2.22.4 UninstallModal).
_UNINSTALL_OPTIONS_JS = r"""
(async (appName, runner) => {
  %s
  const api = window.api;
  if (!api) return { error: 'not-ready' };
  const gi = await until(() => api.getGameInfo(appName, runner), 30);
  if (!gi) return { error: 'unknown-game' };
  if (!gi.is_installed) return { error: 'not-installed' };
  const native = !!(await api.isNative({ appName, runner }));
  const out = { title: gi.title, installPath: (gi.install && gi.install.install_path) || '', native,
                prefix: '', prefixShared: false, epicBusy: false };
  if (!native && gi.install && String(gi.install.platform || '').toLowerCase() === 'windows') {
    const gs = await api.requestGameSettings(appName);
    const def = await api.requestGameSettings('default');
    out.prefix = (gs && gs.winePrefix) || '';
    out.prefixShared = !!def && out.prefix === def.winePrefix;
  }
  if (runner === 'legendary') {   // Heroic can't uninstall an Epic game while another one installs
    const q = await api.getDMQueueInformation();
    out.epicBusy = q.state === 'running' && (q.elements || []).some(e => e.params.runner === 'legendary');
  }
  return out;
})(%s, %s)
"""

# Start the uninstall. Heroic's call only returns when it's finished, so don't wait for
# it; progress comes through the status events.
_UNINSTALL_JS = r"""
(async (appName, runner, removePrefix, removeSettings) => {
  const api = window.api;
  if (!api) return 'not-ready';
  const p = (window.__heroicRatingsProgress || {})[appName];
  if (p && p.status === 'uninstalling') return 'started';   // already running (e.g. a retried request)
  const gi = await api.getGameInfo(appName, runner);
  if (gi && !gi.is_installed) return 'done';
  const done = api.uninstall(appName, runner, removePrefix, removeSettings).then(() => 'done', () => 'error');
  return await Promise.race([done, new Promise(r => setTimeout(() => r('started'), 3000))]);
})(%s, %s, %s, %s)
"""

# Heroic only sends progress as events, so keep the latest one per game in a small
# listener, and read the download queue.
_DOWNLOADS_JS = r"""
(async () => {
  const api = window.api;
  if (!window.__heroicRatingsProgress) {
    window.__heroicRatingsProgress = {};
    const keep = (e, st) => {
      if (st && st.appName) window.__heroicRatingsProgress[st.appName] =
        Object.assign({}, window.__heroicRatingsProgress[st.appName], st, { t: Date.now() });
    };
    api.onProgressUpdate(keep);
    api.handleGameStatus(keep);
  }
  const q = await api.getDMQueueInformation();
  const now = Date.now();
  return {
    state: q.state,
    queue: (q.elements || []).map(e => ({ appName: e.params.appName, runner: e.params.runner, type: e.type })),
    progress: window.__heroicRatingsProgress,
    uninstalling: Object.values(window.__heroicRatingsProgress)
      .filter(p => p.status === 'uninstalling' && now - p.t < 30 * 60 * 1000)
      .map(p => ({ appName: p.appName, runner: p.runner })),
  };
})()
"""

_LAUNCH_RE = re.compile(r"heroic://launch(?:/(?P<r1>[\w-]+)/(?P<a1>[^\s\"'&?]+)|\?(?P<q>[^\s\"']+))")


class Heroic2(HeroicAdapter):
    name = "Heroic 2.x"
    majors = (2,)
    tested = "2.22.4"

    store_labels = {"legendary": "Epic", "gog": "GOG", "nile": "Amazon", "sideload": "Other"}
    LIBRARY_FILES = [
        ("store_cache/legendary_library.json", "legendary"),
        ("store_cache/gog_library.json", "gog"),
        ("store_cache/nile_library.json", "nile"),
        ("sideload_apps/library.json", "sideload"),
        ("gog_store/library.json", "gog"),  # older Heroic versions
    ]
    INSTALLED_FILES = ["legendaryConfig/legendary/installed.json", "gog_store/installed.json",
                       "nile_config/nile/installed.json"]

    # -- files
    def looks_like_config_dir(self, d: Path) -> bool:
        return (d / "store_cache").is_dir() or (d / "gog_store").is_dir()

    def watched_files(self) -> list[str]:
        return [rel for rel, _ in self.LIBRARY_FILES] + self.INSTALLED_FILES

    def library_entries(self, heroic_dir: Path):
        seen = set()
        for rel, runner in self.LIBRARY_FILES:
            data = util.read_json(heroic_dir / rel)
            if data is None:
                continue
            for e in _iter_entries(data):
                seen.add(e.get("runner") or runner)
                yield e, runner
        if "legendary" not in seen:
            for e in _legendary_metadata_fallback(heroic_dir):
                yield e, "legendary"

    def installed_ids(self, heroic_dir: Path) -> set[str]:
        ids: set[str] = set()
        leg = util.read_json(heroic_dir / "legendaryConfig/legendary/installed.json", {})
        if isinstance(leg, dict):
            ids.update(f"legendary:{k}" for k in leg)
        gog = util.read_json(heroic_dir / "gog_store/installed.json", {})
        if isinstance(gog, dict):
            for g in gog.get("installed", []):
                if isinstance(g, dict) and g.get("appName"):
                    ids.add(f"gog:{g['appName']}")
        nile = util.read_json(heroic_dir / "nile_config/nile/installed.json", [])
        if isinstance(nile, list):
            for g in nile:
                if isinstance(g, dict) and g.get("id"):
                    ids.add(f"nile:{g['id']}")
        return ids

    # -- launch URLs
    def launch_url(self, runner: str, app_name: str, show_gui: bool) -> str:
        params = {"appName": app_name, "runner": runner}
        if not show_gui:
            params["gui"] = "false"
        return "heroic://launch?" + urllib.parse.urlencode(params)

    def parse_launch(self, text: str) -> tuple[str, str] | None:
        m = _LAUNCH_RE.search(text or "")
        if not m:
            return None
        if m.group("q"):
            q = urllib.parse.parse_qs(m.group("q"))
            app, runner = (q.get("appName") or [None])[0], (q.get("runner") or [None])[0]
            return (runner, app) if app and runner else None
        return m.group("r1"), urllib.parse.unquote(m.group("a1"))

    # -- Heroic's window
    required_api = ("getGameInfo", "getInstallInfo", "requestAppSettings", "requestGameSettings",
                    "getAlternativeWine", "writeConfig", "install", "uninstall", "isNative",
                    "getDMQueueInformation", "onProgressUpdate", "handleGameStatus")
    ready_js = "document.readyState === 'complete' && !!window.api && location.protocol !== 'about:'"

    def install_options_js(self, runner, app_name, platform):
        return _INSTALL_OPTIONS_JS % (_JS_UNTIL, json.dumps(app_name), json.dumps(runner), json.dumps(platform))

    def install_js(self, runner, app_name, choice):
        params = {"appName": app_name, "runner": runner, "path": choice["path"],
                  "platformToInstall": choice["platform"], "installDlcs": choice["dlcs"],
                  "sdlList": choice["sdl"], "installLanguage": choice["language"]}
        return _INSTALL_WITH_JS % (_JS_UNTIL, json.dumps(params), json.dumps(choice["wine"]),
                                   json.dumps(choice["sharedPrefix"]))

    def uninstall_options_js(self, runner, app_name):
        return _UNINSTALL_OPTIONS_JS % (_JS_UNTIL, json.dumps(app_name), json.dumps(runner))

    def uninstall_js(self, runner, app_name, remove_prefix, remove_settings):
        return _UNINSTALL_JS % (json.dumps(app_name), json.dumps(runner),
                                json.dumps(bool(remove_prefix)), json.dumps(bool(remove_settings)))

    def downloads_js(self):
        return _DOWNLOADS_JS

    def normalise_downloads(self, raw) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        progress = {}
        for app, st in (raw.get("progress") or {}).items():
            pr = (st or {}).get("progress") if isinstance(st, dict) else None
            if isinstance(pr, dict):
                progress[app] = {"percent": pr.get("percent"), "speed": pr.get("downSpeed"), "eta": pr.get("eta")}
        return {"state": raw.get("state"),
                "queue": [{"appName": q.get("appName"), "runner": q.get("runner")}
                          for q in raw.get("queue") or [] if isinstance(q, dict)],
                "progress": progress,
                "uninstalling": [{"appName": u.get("appName"), "runner": u.get("runner")}
                                 for u in raw.get("uninstalling") or [] if isinstance(u, dict)]}


def _iter_entries(obj):
    """Yield game-like dicts (having app_name + title) without descending into them."""
    if isinstance(obj, dict):
        if "app_name" in obj and "title" in obj:
            yield obj
            return
        for v in obj.values():
            yield from _iter_entries(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_entries(v)


def _legendary_metadata_fallback(heroic: Path):
    """Old/missing store cache: read raw Epic metadata written by legendary."""
    meta_dir = heroic / "legendaryConfig/legendary/metadata"
    if not meta_dir.is_dir():
        return
    for f in meta_dir.glob("*.json"):
        m = util.read_json(f, {})
        md = m.get("metadata") or {}
        if not m.get("app_name") or md.get("mainGameItem"):  # skip DLC
            continue
        art = ""
        for img in md.get("keyImages", []):
            if img.get("type") in ("DieselGameBoxTall", "DieselGameBox"):
                art = img.get("url", "")
                if img.get("type") == "DieselGameBoxTall":
                    break
        yield {"app_name": m["app_name"], "title": m.get("app_title") or md.get("title", m["app_name"]),
               "runner": "legendary", "art_square": art}
