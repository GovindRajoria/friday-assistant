import { defineConfig } from "vitest/config";

// A separate config from electron.vite.config.ts: vitest only auto-loads
// vite.config.ts / vitest.config.ts, and the reducer under test is plain
// TypeScript with no Electron or DOM dependency, so "node" is enough.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
