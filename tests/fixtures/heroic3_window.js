// A stand-in for Heroic 3.x's window API (the parts the Heroic3 adapter uses), from the
// 3.0.0-beta.1 source (src/preload/api, src/common/types/ipc.ts): the same names,
// arguments and shapes as 2.22.4, plus Steam games (runner "steam") and "pausedAt" on
// download queue items. Calls are recorded in window.calls.
window.calls = [];
const games = {
  alpha: { title: "Alpha Quest", is_installed: true, install: { install_path: "/games/Alpha", platform: "Windows" } },
  beta: { title: "Bravo Racer: Deluxe", is_installed: false, is_linux_native: true },
  "620": { title: "Portal 2", is_installed: true, install: { install_path: "/steam/common/Portal 2", platform: "linux" } },
};
const queue = [];
const listeners = [];
const emit = st => listeners.forEach(f => f(null, st));
window.api = {
  getGameInfo: async (app, runner) => games[app] ? { app_name: app, runner, ...games[app] } : null,
  getInstallInfo: async (app, runner, platform) => ({
    manifest: { download_size: 1000, disk_size: 3000, languages: ["fr", "en"] },
    game: { owned_dlc: [{ app_name: "beta_dlc", title: "Bravo Soundtrack" }] },
  }),
  requestAppSettings: async () => ({ defaultInstallPath: "/home/deck/Games/Heroic", winePrefix: "/p/default",
                                     defaultWinePrefixDir: "/p" }),
  requestGameSettings: async app => app === "default" ? { winePrefix: "/p/default" }
                                                      : { winePrefix: "/p/default", wineVersion: { bin: "/w/ge" }, other: 1 },
  getAlternativeWine: async () => [{ name: "Proton GE", type: "proton", bin: "/w/ge" }, { name: "Wine 9", type: "wine", bin: "/w/wine" }],
  getGameOverride: async () => ({}),
  getGameSdl: async () => [],
  writeConfig: async a => { calls.push(["writeConfig", a]); },
  install: async p => {
    calls.push(["install", { appName: p.appName, runner: p.runner, path: p.path, platformToInstall: p.platformToInstall,
                             installDlcs: p.installDlcs, installLanguage: p.installLanguage, hasGameInfo: !!p.gameInfo }]);
    queue.push({ params: { appName: p.appName, runner: p.runner }, type: "install", pausedAt: undefined });
    emit({ appName: p.appName, runner: p.runner, status: "installing",
           progress: { percent: 42, downSpeed: 12.5, eta: "00:01:00" } });
  },
  uninstall: (app, runner, prefix, settings) => {
    calls.push(["uninstall", { app, runner, prefix, settings }]);
    emit({ appName: app, runner, status: "uninstalling" });
    return new Promise(() => {});        // Heroic only answers when it's finished
  },
  isNative: async () => false,
  getDMQueueInformation: async () => ({ state: queue.length ? "running" : "idle", elements: queue }),
  onProgressUpdate: f => listeners.push(f),
  handleGameStatus: f => listeners.push(f),
};
