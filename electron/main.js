// Heroic Ratings desktop shell: starts the bundled Python backend and shows the
// UI in its own (fullscreen by default) window. No system browser or Python needed.
//
//   runtime/electron/electron --no-sandbox electron [--windowed]
"use strict";
const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const WINDOWED = process.argv.includes("--windowed");
const SCREENSHOT = process.env.HEROIC_RATINGS_SCREENSHOT; // test hook: capture & quit
const DEFAULT_PORT = Number(process.env.HEROIC_RATINGS_PORT || 8787);

let backend = null;
let win = null;
let quitting = false;

app.commandLine.appendSwitch("disable-features", "Translate,MediaRouter,HardwareMediaKeyHandling");
app.commandLine.appendSwitch("disable-http-cache"); // UI is local; keeps updates instant
// Keep Electron's own state with the app data (the app folder, or ~/.local/share/... for the AppImage).
app.setPath("userData", path.join(process.env.HEROIC_RATINGS_DATA || path.join(ROOT, "data"), "electron"));

function log(...a) { console.error("[heroic-ratings]", ...a); }

function pythonBin() {
  const bundled = path.join(ROOT, "runtime", "python", "bin", "python3");
  return fs.existsSync(bundled) ? bundled : "python3";
}

function caBundle() {
  return ["/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem", "/etc/pki/tls/certs/ca-bundle.crt"]
    .find(p => fs.existsSync(p));
}

function status(port) {
  return new Promise(resolve => {
    const req = http.get({ host: "127.0.0.1", port, path: "/api/status", timeout: 1500 }, res => {
      let body = "";
      res.on("data", c => (body += c));
      res.on("end", () => { try { resolve(JSON.parse(body).app === "heroic-ratings"); } catch { resolve(false); } });
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

function portFree(port) {
  return new Promise(resolve => {
    const s = net.createServer().once("error", () => resolve(false))
      .once("listening", () => s.close(() => resolve(true))).listen(port, "127.0.0.1");
  });
}

function anyPort() {
  return new Promise(resolve => {
    const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

async function startBackend() {
  if (await status(DEFAULT_PORT)) { log("reusing running backend on", DEFAULT_PORT); return DEFAULT_PORT; }
  const port = (await portFree(DEFAULT_PORT)) ? DEFAULT_PORT : await anyPort();
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1" };
  delete env.ELECTRON_RUN_AS_NODE;
  if (!env.SSL_CERT_FILE && caBundle()) env.SSL_CERT_FILE = caBundle();
  const py = pythonBin();
  log("backend:", py, "port", port);
  backend = spawn(py, [path.join(ROOT, "heroic_ratings.py"), "--port", String(port), ...extraBackendArgs()],
    { cwd: ROOT, env, stdio: ["ignore", "inherit", "inherit"] });
  backend.on("exit", code => {
    log("backend exited", code);
    backend = null;
    if (!quitting) app.quit(); // "Quit" in the menu stops the backend -> close the window too
  });
  for (let i = 0; i < 100; i++) {         // wait up to ~10 s
    if (await status(port)) return port;
    if (!backend) throw new Error("backend failed to start");
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error("backend did not respond");
}

function extraBackendArgs() {
  const out = [];
  const i = process.argv.indexOf("--heroic-dir");
  if (i > 0 && process.argv[i + 1]) out.push("--heroic-dir", process.argv[i + 1]);
  if (process.argv.includes("--no-sync")) out.push("--no-sync");
  return out;
}

function stopBackend() {
  quitting = true;
  if (backend) { try { backend.kill("SIGTERM"); } catch { /* already gone */ } }
}

async function createWindow() {
  let port;
  try {
    port = await startBackend();
  } catch (e) {
    log(e.message);
    app.exit(1);
    return;
  }
  const base = `http://127.0.0.1:${port}/`;
  win = new BrowserWindow({
    width: 1280, height: 800,
    fullscreen: !WINDOWED && !SCREENSHOT,
    show: !SCREENSHOT,
    title: "Heroic Ratings",
    backgroundColor: "#0e141b",
    autoHideMenuBar: true,
    icon: path.join(ROOT, "web", "icon.png"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, offscreen: !!SCREENSHOT,
                      preload: path.join(__dirname, "preload.js") },
  });
  win.setMenu(null);
  // Only ever show our own local page.
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(base)) e.preventDefault(); });
  win.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    if (input.key === "F11") { win.setFullScreen(!win.isFullScreen()); e.preventDefault(); }
    if (input.control && input.key.toLowerCase() === "q") { app.quit(); e.preventDefault(); }
  });
  // "Step aside" for Heroic: leave fullscreen and minimise so Heroic's own windows
  // (its install dialog) are visible. Fullscreen comes back when the player returns.
  let steppedAside = false;
  ipcMain.on("step-aside", e => {
    if (e.sender !== win.webContents || win.isMinimized()) return;
    const wasFull = win.isFullScreen();
    if (wasFull) win.setFullScreen(false);
    setTimeout(() => {
      if (win.isDestroyed()) return;
      win.minimize();
      steppedAside = wasFull;  // armed only once minimised
      log("stepped aside for Heroic (minimised:", win.isMinimized() + ")");
    }, 150);
  });
  const comeBack = () => {
    if (!steppedAside || win.isMinimized()) return;
    steppedAside = false;
    win.setFullScreen(true);
  };
  win.on("restore", comeBack);
  win.on("focus", comeBack);
  await win.loadURL(base);

  if (SCREENSHOT) {
    win.setContentSize(1280, 720);
    await new Promise(r => setTimeout(r, 3000));
    fs.writeFileSync(SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    log("screenshot saved", SCREENSHOT);
    app.quit();
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
  app.whenReady().then(createWindow);
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", stopBackend);
  process.on("SIGTERM", () => app.quit());
  process.on("SIGINT", () => app.quit());
}
