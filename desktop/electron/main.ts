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
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import type { BackendReport } from "./api";

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

// Where the Python backend lives, which is a different question in a dev
// checkout, an installed app, and a portable executable.
//
// The installer ships only the shell — bundling a 1.1 GB virtualenv, a vision
// model and a 4.9 GB language model is not a download anyone wants — so a
// packaged HUD has to find an existing backend. It tries several places in
// order, and remembers the answer, because being told once should be enough.
//
// `PORTABLE_EXECUTABLE_DIR` is not optional here, it is the fix for a real
// failure. electron-builder's portable target unpacks itself into a fresh
// `%TEMP%\<random>` directory on every launch and runs from there, so
// `dirname(app.getPath("exe"))` is that throwaway folder and "beside the
// executable" can never resolve to anything. Reported from the field as
// `...\AppData\Local\Temp\3HOsQMP2od...\FRIDAY_CORE\friday_env\...`.
// electron-builder sets this variable to where the .exe actually lives.
function backendConfigFile(): string {
  return path.join(app.getPath("userData"), "backend.json");
}

function rememberedCoreDir(): string | null {
  try {
    const parsed = JSON.parse(readFileSync(backendConfigFile(), "utf8")) as { coreDir?: string };
    return parsed.coreDir ?? null;
  } catch {
    // No file yet, or an unreadable one. Either way there is nothing to
    // remember and the other candidates still apply.
    return null;
  }
}

function rememberCoreDir(dir: string): void {
  try {
    writeFileSync(backendConfigFile(), JSON.stringify({ coreDir: dir }, null, 2), "utf8");
  } catch (error) {
    // Not fatal: the app works for this session, it just asks again next time.
    console.error("[friday-desktop] could not save the backend location:", error);
  }
}

// A directory is only a backend if it has an interpreter in it. Checking for
// the venv rather than for the folder name is what makes the picker below
// able to reject a wrong choice immediately instead of failing 60s later.
function looksLikeCore(dir: string): boolean {
  return existsSync(path.join(dir, "friday_env", "Scripts", "python.exe"))
    || existsSync(path.join(dir, "friday_env", "bin", "python"));
}

function candidateCoreDirs(): string[] {
  const candidates: string[] = [];
  if (process.env.FRIDAY_CORE_DIR) candidates.push(path.resolve(process.env.FRIDAY_CORE_DIR));
  const remembered = rememberedCoreDir();
  if (remembered) candidates.push(remembered);
  if (process.env.PORTABLE_EXECUTABLE_DIR) {
    candidates.push(path.join(process.env.PORTABLE_EXECUTABLE_DIR, "FRIDAY_CORE"));
  }
  if (!app.isPackaged) {
    // electron-vite writes this file to out/main under both `dev` and
    // `build` — dev builds to disk too, just with a watcher — so __dirname
    // is out/main either way and three levels up is the repo root.
    candidates.push(path.join(path.resolve(__dirname, "..", "..", ".."), "FRIDAY_CORE"));
  }
  candidates.push(path.join(path.dirname(app.getPath("exe")), "FRIDAY_CORE"));
  return candidates;
}

function resolveFridayCoreDir(): string {
  const candidates = candidateCoreDirs();
  // The first one that actually holds an interpreter wins. Falling back to
  // the last candidate rather than to nothing keeps the error message able
  // to name a concrete path.
  return candidates.find(looksLikeCore) ?? candidates[candidates.length - 1];
}

// Mutable, because the picker below can change the answer at runtime and
// everything downstream — the spawn, the error report, the retry — has to
// see the new one.
let FRIDAY_CORE_DIR = resolveFridayCoreDir();
let PYTHON_EXE = path.join(FRIDAY_CORE_DIR, "friday_env", "Scripts", "python.exe");

function useCoreDir(dir: string): void {
  FRIDAY_CORE_DIR = dir;
  PYTHON_EXE = path.join(dir, "friday_env", "Scripts", "python.exe");
}

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

// What launch actually did, so the HUD can explain a dead link instead of
// just reporting one.
//
// This exists because of a real report: someone ran the installer, launched
// the app, and got "Disconnected" with nothing else. The installed shell has
// no backend beside it and no FRIDAY_CORE_DIR, so it could not spawn one —
// which is the documented design, but a HUD that knows exactly why it has
// no backend and says only "disconnected" is withholding the one fact that
// would fix it. Written to a module global and read back over IPC, since
// main is the only side that knows where it looked.
let backendReport: BackendReport = { kind: "starting" };

/** Ask the operator where FRIDAY_CORE is, and validate the answer on the spot.
 *
 * Rejecting a wrong folder immediately, by looking for the virtualenv inside
 * it, is the difference between "that isn't it, try again" and a sixty-second
 * wait for a health probe that was never going to succeed. Returns null if
 * they decline, which is a legitimate choice — the HUD still opens and says
 * what is missing.
 */
async function askForCoreDir(): Promise<string | null> {
  const intro = await dialog.showMessageBox({
    type: "info",
    title: "FRIDAY — backend not found",
    message: "FRIDAY could not find its Python backend.",
    detail:
      "The installer ships the interface only — the backend, its virtual environment and the "
      + "models are far too large to bundle.\n\nPoint FRIDAY at the FRIDAY_CORE folder from your "
      + "checkout and it will be remembered for next time.",
    buttons: ["Locate FRIDAY_CORE…", "Not now"],
    defaultId: 0,
    cancelId: 1,
  });
  if (intro.response !== 0) return null;

  // Loops rather than giving up on a wrong pick — choosing the repo root
  // instead of FRIDAY_CORE is an easy mistake and not worth restarting for.
  for (;;) {
    const picked = await dialog.showOpenDialog({
      title: "Select your FRIDAY_CORE folder",
      properties: ["openDirectory"],
    });
    if (picked.canceled || picked.filePaths.length === 0) return null;

    const chosen = picked.filePaths[0];
    if (looksLikeCore(chosen)) return chosen;
    // Accept the parent too: picking E:\FRIDAY when FRIDAY_CORE is inside it
    // is the obvious near-miss.
    const nested = path.join(chosen, "FRIDAY_CORE");
    if (looksLikeCore(nested)) return nested;

    const retry = await dialog.showMessageBox({
      type: "warning",
      title: "Not a FRIDAY backend",
      message: "That folder has no FRIDAY virtual environment in it.",
      detail: `Looked for friday_env inside ${chosen}.\n\nPick the FRIDAY_CORE folder itself.`,
      buttons: ["Try again", "Cancel"],
      defaultId: 0,
      cancelId: 1,
    });
    if (retry.response !== 0) return null;
  }
}

async function ensureBackend(): Promise<void> {
  if (await probeHealth(2_000)) {
    // Something is already answering on 8756 — attach, and stop here.
    // Spawning a second server under a possibly-different interpreter is
    // exactly the collision this function exists to avoid.
    backendReport = { kind: "attached" };
    return;
  }

  if (!existsSync(PYTHON_EXE)) {
    // Nothing to spawn. Ask, rather than leaving the operator to work out
    // that an environment variable exists — being told the path is the fix
    // is only useful to someone who already knows where to type it.
    const chosen = await askForCoreDir();
    if (chosen) {
      useCoreDir(chosen);
      rememberCoreDir(chosen);
    } else {
      // Distinguished from "spawned and never answered": one is a missing
      // path, the other is a backend that started and failed, and the fix
      // for each is different.
      backendReport = { kind: "missing", coreDir: FRIDAY_CORE_DIR, pythonExe: PYTHON_EXE };
      console.error(`[friday-desktop] no interpreter at ${PYTHON_EXE}; set FRIDAY_CORE_DIR`);
      return;
    }
  }

  spawnServer();
  const healthy = await waitForHealth(HEALTH_POLL_BUDGET_MS);
  if (!healthy) {
    // The window still gets shown — the renderer's own socket connection
    // fails the same way and surfaces it as the HUD's error state, which is
    // the visible failure this function owes the user rather than an
    // indefinitely hidden window.
    backendReport = { kind: "silent", coreDir: FRIDAY_CORE_DIR, timeoutMs: HEALTH_POLL_BUDGET_MS };
    console.error(`[friday-desktop] backend did not answer ${HEALTH_URL} within ${HEALTH_POLL_BUDGET_MS}ms`);
    return;
  }
  backendReport = { kind: "spawned", coreDir: FRIDAY_CORE_DIR };
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
ipcMain.handle("hud:backend-status", (): BackendReport => backendReport);

// The same picker the launch path uses, reachable from the HUD so a missing
// backend can be fixed without restarting the app or opening a shell.
ipcMain.handle("hud:locate-backend", async (): Promise<BackendReport> => {
  const chosen = await askForCoreDir();
  if (!chosen) return backendReport;
  useCoreDir(chosen);
  rememberCoreDir(chosen);
  await ensureBackend();
  return backendReport;
});

ipcMain.handle("hud:health", async (): Promise<{ status: string; skills: string[] }> => {
  const response = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(HEALTH_PROBE_TIMEOUT_MS) });
  if (!response.ok) throw new Error(`health responded ${response.status}`);
  return (await response.json()) as { status: string; skills: string[] };
});

// The operator's biography, read and written straight off disk by main.
//
// Not an HTTP endpoint on the backend, deliberately. The server has no
// authentication and is safe only because it refuses a non-loopback bind;
// adding a write endpoint that persists text to a file widens that surface
// for no gain, since main already knows where FRIDAY_CORE is. The backend
// re-reads the file on every turn, so an edit here takes effect on the next
// question with no restart.
function profilePath(): string {
  return path.join(FRIDAY_CORE_DIR, "config", "about_me.md");
}

ipcMain.handle("hud:read-profile", (): string => {
  try {
    return readFileSync(profilePath(), "utf8");
  } catch {
    // No profile written yet. An empty editor is the right starting point,
    // not an error.
    return "";
  }
});

ipcMain.handle("hud:write-profile", (_event, text: unknown): boolean => {
  if (typeof text !== "string") return false;
  try {
    mkdirSync(path.dirname(profilePath()), { recursive: true });
    writeFileSync(profilePath(), text, "utf8");
    return true;
  } catch (error) {
    console.error("[friday-desktop] could not save the profile:", error);
    return false;
  }
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
