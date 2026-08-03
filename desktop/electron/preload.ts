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
  getBackendStatus() {
    return ipcRenderer.invoke("hud:backend-status");
  },
  locateBackend() {
    return ipcRenderer.invoke("hud:locate-backend");
  },
  readProfile() {
    return ipcRenderer.invoke("hud:read-profile");
  },
  writeProfile(text) {
    return ipcRenderer.invoke("hud:write-profile", text);
  },
  onToggleDictation(handler) {
    // The listener is wrapped rather than passed through, so the renderer
    // never receives Electron's IpcRendererEvent — handing that across the
    // context bridge would leak `sender`, and with it a way to send on
    // arbitrary channels from a context that is supposed to have none.
    const listener = () => handler();
    ipcRenderer.on("hud:toggle-dictation", listener);
    return () => ipcRenderer.removeListener("hud:toggle-dictation", listener);
  },
};

contextBridge.exposeInMainWorld("friday", api);
