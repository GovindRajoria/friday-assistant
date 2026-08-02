// Attach-or-spawn startup, and the reasoning behind it.
//
// Two FastAPI servers listening on the same port has already bitten this
// project once, with a server left running under the wrong Python
// interpreter and invisible until a second one collided with it on 8756.
// The fix is to never assume ownership: probe /health first, and only
// spawn — and therefore only ever kill — a server this process actually
// started. Attaching to an already-running server never touches it on
// quit.
import { type ChildProcess, spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, ipcMain } from "electron";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const SERVER_HOST = "127.0.0.1";
const SERVER_PORT = 8756;
const HEALTH_URL = `http://${SERVER_HOST}:${SERVER_PORT}/health`;

// Cold start (YOLO11-IR + nine skills) measures ~12s on this machine. The
// budget is a cap on how long a launch will wait, not an estimate of how
// long it should take — it exists so a backend that never comes up fails
// visibly within a minute instead of leaving the window hidden forever.
const HEALTH_POLL_BUDGET_MS = 60_000;
const HEALTH_POLL_INTERVAL_MS = 500;

// electron-vite writes main/preload/renderer to sibling directories under
// out/ in both `electron-vite dev` and `electron-vite build` — dev is not
// an in-memory run of electron/main.ts, it builds this file to disk too,
// just with a watcher. __dirname is therefore out/main in both modes, and
// three levels up from there lands on the repo root (desktop/../.. is
// FRIDAY, the parent of both desktop/ and FRIDAY_CORE/).
const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const FRIDAY_CORE_DIR = path.join(REPO_ROOT, "FRIDAY_CORE");
const PYTHON_EXE = path.join(FRIDAY_CORE_DIR, "friday_env", "Scripts", "python.exe");

let mainWindow: BrowserWindow | null = null;

// Set only when this process spawned the server itself; stays null on the
// attach path. Every kill path below reads this instead of re-probing at
// quit time — re-deriving "do we own this" from scratch right before
// killing is how a double-kill or a killed-someone-else's-process bug
// creeps in later.
let spawnedServer: ChildProcess | null = null;

async function probeHealth(timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(HEALTH_URL, { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealth(budgetMs: number): Promise<boolean> {
  const deadline = Date.now() + budgetMs;
  while (Date.now() < deadline) {
    if (await probeHealth(HEALTH_POLL_INTERVAL_MS)) return true;
    await sleep(HEALTH_POLL_INTERVAL_MS);
  }
  return false;
}

function spawnServer(): void {
  spawnedServer = spawn(PYTHON_EXE, ["-m", "server.app"], {
    cwd: FRIDAY_CORE_DIR,
    stdio: "ignore",
  });
  spawnedServer.on("exit", () => {
    spawnedServer = null;
  });
  spawnedServer.on("error", (error) => {
    // ENOENT here almost always means friday_env wasn't set up where
    // expected — surfacing it beats a silent hang waiting on /health.
    console.error(`[friday-desktop] failed to launch ${PYTHON_EXE}:`, error);
    spawnedServer = null;
  });
}

function killSpawnedServer(): void {
  const child = spawnedServer;
  if (!child || child.pid === undefined) return;
  // Cleared before the kill itself, not after: before-quit, window-all-
  // closed and process-exit can all fire in the same shutdown, and a
  // second call here must see nothing to do rather than signal an already-
  // dead pid a second time.
  spawnedServer = null;
  if (process.platform === "win32") {
    // child.kill() does not reliably take the process tree on Windows —
    // it can leave the actual python.exe running under the node handle's
    // now-gone parent. taskkill /T walks the tree explicitly.
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
  } else {
    child.kill("SIGTERM");
  }
}

async function ensureBackend(): Promise<void> {
  if (await probeHealth(2_000)) {
    // Something is already answering on 8756 — attach, and stop here.
    // Spawning a second server under a possibly-different interpreter is
    // exactly the collision this function exists to avoid.
    return;
  }
  spawnServer();
  const healthy = await waitForHealth(HEALTH_POLL_BUDGET_MS);
  if (!healthy) {
    // The window still gets shown below — the renderer's own socket
    // connection will fail the same way and surface it as the HUD's error
    // state, which is the visible failure this function owes the user
    // rather than an indefinitely hidden window.
    console.error(`[friday-desktop] backend did not answer ${HEALTH_URL} within ${HEALTH_POLL_BUDGET_MS}ms`);
  }
}

function createWindow(): BrowserWindow {
  return new BrowserWindow({
    width: 420,
    height: 640,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    // transparent + resizable is unreliable on Windows (resize handles
    // fight the transparency compositing) — fixed size sidesteps it.
    resizable: false,
    // Not shown until ensureBackend() resolves, one way or the other —
    // never a blank transparent window sitting on screen while the model
    // and nine skills finish loading.
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // backgroundColor is deliberately left unset: transparent already
  // handles it, and setting one (even 'rgba(0,0,0,0)' explicitly) is a
  // known source of compositing glitches with frameless transparent
  // windows on Windows.
}

async function loadRenderer(win: BrowserWindow): Promise<void> {
  // electron-vite sets this env var only under `electron-vite dev`, where
  // the renderer is served by Vite's dev server for HMR. `electron-vite
  // preview` / a plain launch of the built app has no such server, so it
  // loads the built HTML from disk instead.
  const devServerUrl = process.env.ELECTRON_RENDERER_URL;
  if (devServerUrl) {
    await win.loadURL(devServerUrl);
  } else {
    await win.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
}

ipcMain.on("hud:set-ignore-mouse-events", (event, ignore: boolean, options?: { forward: boolean }) => {
  BrowserWindow.fromWebContents(event.sender)?.setIgnoreMouseEvents(Boolean(ignore), options);
});

app.whenReady().then(async () => {
  mainWindow = createWindow();
  // Loading starts immediately and races with the backend probe rather
  // than waiting on it — the renderer's own socket hook begins trying to
  // connect the moment it mounts and will succeed whenever the backend
  // answers, whether that lands before or after ensureBackend() returns.
  const loaded = loadRenderer(mainWindow);
  await ensureBackend();
  await loaded;
  mainWindow.show();
});

app.on("before-quit", killSpawnedServer);
app.on("window-all-closed", () => {
  killSpawnedServer();
  if (process.platform !== "darwin") app.quit();
});
process.on("exit", killSpawnedServer);
