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
// Used only by the HUD's own systems-panel poll, which runs long after
// startup against a backend already known to be up. Generous compared to
// the launch probe because a slow answer there should still be an answer,
// not a panel that flickers to "unreachable" while a turn is in flight.
const HEALTH_PROBE_TIMEOUT_MS = 4_000;

// Where the Python backend lives, which is not the same question in a dev
// checkout as in an installed app.
//
// Unpackaged, electron-vite has written this file to out/main in both `dev`
// and `build` — dev is not an in-memory run of electron/main.ts, it builds
// to disk too, just with a watcher. So __dirname is out/main either way, and
// three levels up lands on the repo root (desktop/../.. is FRIDAY, the parent
// of both desktop/ and FRIDAY_CORE/).
//
// Packaged, that resolution walks into app.asar and finds nothing. The
// installer ships only the shell — bundling a 1.1 GB virtualenv, a vision
// model and a 4.9 GB language model into an installer is not a thing anyone
// wants to download — so an installed HUD has to be told where an existing
// backend is. FRIDAY_CORE_DIR is that answer, and the directory beside the
// executable is the fallback for keeping the two side by side.
function resolveFridayCoreDir(): string {
  const fromEnv = process.env.FRIDAY_CORE_DIR;
  if (fromEnv) return path.resolve(fromEnv);
  if (!app.isPackaged) return path.join(path.resolve(__dirname, "..", "..", ".."), "FRIDAY_CORE");
  return path.join(path.dirname(app.getPath("exe")), "FRIDAY_CORE");
}

const FRIDAY_CORE_DIR = resolveFridayCoreDir();
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

// An ordinary desktop window, not a floating overlay.
//
// It was a transparent, always-on-top, click-through panel, and that design
// failed on the operator's machine in three connected ways. The window starts
// click-through and only becomes interactive on a `mouseenter` in the
// renderer — but Electron hands mouse events over a `-webkit-app-region:
// drag` surface to the OS instead of to the page, so a pointer crossing the
// title bar makes those enter/leave events unreliable and the flag latches.
// Latched on, nothing in the window can be dragged or clicked. Latched off,
// an always-on-top window silently swallows every click inside its rectangle,
// so the desktop underneath becomes unusable while FRIDAY is running. There
// were also no window controls at all, because a frameless overlay was never
// meant to be minimised.
//
// So: opaque, resizable, movable, with real controls, and always-on-top
// demoted to a button the operator presses when they actually want it.
function createWindow(): BrowserWindow {
  return new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 940,
    minHeight: 620,
    // Still frameless — the title bar is drawn in the renderer so it matches
    // the rest of the instrument panel. What changed is that it now carries
    // minimise, maximise and close, and its drag region is not competing
    // with a click-through flag.
    frame: false,
    // transparent: false is what makes resizable: true reliable here. The two
    // fight on Windows, which is why the overlay was a fixed size; dropping
    // transparency is what buys back resizing and dragging.
    transparent: false,
    resizable: true,
    maximizable: true,
    // Opt-in, via the pin button in the title bar. Defaulting a window that
    // covers a third of the screen to always-on-top is the behaviour that
    // made the machine unusable.
    alwaysOnTop: false,
    // Matches the chassis so there is no white flash between the window
    // appearing and the renderer painting. Safe to set now that the window
    // is not transparent — on a transparent window this was a known source
    // of compositing glitches.
    backgroundColor: "#05080c",
    // Not shown until ensureBackend() resolves, one way or the other — never
    // an empty window sitting on screen while the model and every skill
    // finish loading.
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
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

// Window controls, since the frame that would normally provide them is off.
ipcMain.on("hud:minimize", (event) => {
  BrowserWindow.fromWebContents(event.sender)?.minimize();
});

ipcMain.on("hud:toggle-maximize", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return;
  // A toggle, not a one-way maximise — otherwise the button strands the
  // window at full screen with no way back.
  if (win.isMaximized()) win.unmaximize();
  else win.maximize();
});

ipcMain.on("hud:close", (event) => {
  // close(), not app.exit(). The backend kill hangs off `before-quit`,
  // `window-all-closed` and `process.on('exit')`, and skipping those is how
  // this project orphaned a server twice already.
  BrowserWindow.fromWebContents(event.sender)?.close();
});

// Returns the state the window actually ended up in rather than the state
// that was asked for, so the pin button reflects reality instead of a guess.
ipcMain.handle("hud:toggle-always-on-top", (event): boolean => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return false;
  win.setAlwaysOnTop(!win.isAlwaysOnTop());
  return win.isAlwaysOnTop();
});

// The HUD's systems panel asks for this on a timer. The throw is deliberate:
// the renderer has to be able to tell "the backend answered with no skills"
// apart from "the backend did not answer", and a resolved empty list reads
// identically to the former.
ipcMain.handle("hud:health", async (): Promise<{ status: string; skills: string[] }> => {
  const response = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(HEALTH_PROBE_TIMEOUT_MS) });
  if (!response.ok) throw new Error(`health responded ${response.status}`);
  return (await response.json()) as { status: string; skills: string[] };
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
