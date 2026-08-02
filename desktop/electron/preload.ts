// Runs with contextIsolation: true and nodeIntegration: false — the only
// thing the renderer gets from Node or Electron is whatever is explicitly
// placed on window.friday here. No remote module, no direct ipcRenderer
// exposure.
import { contextBridge, ipcRenderer } from "electron";
import type { FridayApi } from "./api";

const api: FridayApi = {
  setIgnoreMouseEvents(ignore, options) {
    ipcRenderer.send("hud:set-ignore-mouse-events", ignore, options);
  },
};

contextBridge.exposeInMainWorld("friday", api);
