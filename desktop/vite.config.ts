import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Renderer-only build. electron.vite.config.ts additionally bundles the
// main and preload processes and is what `npm run build` (electron-vite
// build) uses locally; this file exists so the CI desktop job can run a
// plain `vite build` — Electron itself is never launched on a headless
// runner, so there is nothing to gain from building main/preload there.
// Output goes to dist/, kept separate from electron-vite's out/ in
// .gitignore.
export default defineConfig({
  root: "src",
  build: {
    outDir: resolve(__dirname, "dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, "src/index.html"),
    },
  },
  plugins: [react()],
});
