// A stand-in for Heroic 2.x's window API (the parts the Heroic2 adapter uses), with
// the same names, arguments and result shapes as Heroic 2.22.4. Calls are recorded in
// window.calls. A future Heroic version gets its own file like this one.
window.calls = [];
const games = {
  alpha: { title: "Alpha Quest", is_installed: true, install: { install_path: "/games/Alpha", platform: "Windows" } },
  beta: { title: "Bravo Racer: Deluxe", is_installed: false, is_linux_native: true },
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
    queue.push({ params: { appName: p.appName, runner: p.runner }, type: "install" });
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
