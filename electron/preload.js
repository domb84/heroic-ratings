// Gives the page one small, fixed bridge to its window. Nothing else from
// Electron or Node is exposed.
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("shell", {
  // Get out of Heroic's way (its install dialog opens in its own window,
  // which would otherwise sit behind our fullscreen one).
  stepAside: () => ipcRenderer.send("step-aside"),
  // The clipboard's text, for the Paste key.
  readClipboard: () => ipcRenderer.invoke("read-clipboard"),
});
