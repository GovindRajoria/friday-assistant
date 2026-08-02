import { resolve } from "node:path";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      lib: { entry: resolve(__dirname, "electron/main.ts") },
      // electron-vite's dev-mode entry check (ensureElectronEntryFile) looks
      // for out/main/index.js specifically, regardless of the source
      // entry's basename. The source file stays electron/main.ts, as
      // specified, but the emitted bundle needs this fixed name or `dev`
      // fails before Electron ever launches.
      rollupOptions: { output: { entryFileNames: "index.js" } },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      // Forced to cjs — verified live, not a precaution. With the default
      // ESM output (preload.mjs) and webPreferences.sandbox: true, Electron
      // 43's sandboxed preload loader threw "Cannot use import statement
      // outside a module" and silently discarded the whole preload: no
      // contextBridge exposure, window.friday undefined, and every event
      // handler that read it uncaught-threw once React committed. Sandboxed
      // preload scripts have to be CommonJS; the source stays ESM
      // (electron/preload.ts, import/export), only the bundle format
      // changes.
      lib: { entry: resolve(__dirname, "electron/preload.ts"), formats: ["cjs"] },
    },
  },
  renderer: {
    root: "src",
    build: {
      rollupOptions: {
        input: resolve(__dirname, "src/index.html"),
      },
    },
    plugins: [react()],
  },
});
