// Runs with contextIsolation: true and nodeIntegration: false — the only
// thing the renderer gets from Node or Electron is whatever is explicitly
// placed on window.friday here. No remote module, no direct ipcRenderer
// exposure.
import { contextBridge, ipcRenderer } from "electron";
import type { FridayApi } from "./api";

const api: FridayApi = {
  minimize() {
    ipcRenderer.send("hud:minimize");
  },
  toggleMaximize() {
    ipcRenderer.send("hud:toggle-maximize");
  },
  close() {
    ipcRenderer.send("hud:close");
  },
  toggleAlwaysOnTop() {
    return ipcRenderer.invoke("hud:toggle-always-on-top");
  },
  // invoke/handle, not send/on. Click-through is fire-and-forget and has
  // nothing to return; health does, and copying that channel's shape would
  // hand the renderer a function that quietly resolves to undefined.
  getHealth() {
    return ipcRenderer.invoke("hud:health");
  },
};

contextBridge.exposeInMainWorld("friday", api);
