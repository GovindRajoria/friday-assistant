// Shape of the bridge exposed on window.friday. Kept in its own file, with
// no Node or DOM specific imports, so both electron/preload.ts (which
// implements it) and src/window.d.ts (which declares it as a global) can
// import the type without pulling either side's lib set into the other's
// tsconfig.
export interface HealthReport {
  status: string;
  skills: string[];
}

// What launch did about the backend, so a dead link can explain itself.
//
// `missing` and `silent` are kept apart deliberately: the first means there
// was no interpreter to start (an installed shell with no FRIDAY_CORE beside
// it and no FRIDAY_CORE_DIR), the second means one started and never
// answered. Collapsing them into "backend unavailable" would hide the only
// difference that changes what the operator has to do next.
export type BackendReport =
  | { kind: "starting" }
  | { kind: "attached" }
  | { kind: "spawned"; coreDir: string }
  | { kind: "missing"; coreDir: string; pythonExe: string }
  | { kind: "silent"; coreDir: string; timeoutMs: number };

export interface FridayApi {
  // The window is frameless, so the controls a frame would have provided are
  // drawn in the renderer and bridged back to the BrowserWindow that owns it.
  minimize(): void;
  toggleMaximize(): void;
  close(): void;

  // Resolves to the state the window actually ended up in, not the state
  // that was requested, so the pin button can render the truth.
  toggleAlwaysOnTop(): Promise<boolean>;

  // GET /health, performed in the main process rather than by the renderer.
  //
  // Not a stylistic choice: a renderer fetch to http://127.0.0.1:8756 is a
  // cross-origin request, and the only way to allow it would be to put CORS
  // headers on a server that has no authentication at all. Doing that would
  // let any web page the operator happens to visit read this endpoint. Main
  // runs in Node, where same-origin policy does not apply, so the server's
  // surface stays exactly as narrow as it was.
  //
  // Rejects rather than resolving when the backend is unreachable, so the
  // caller can distinguish "no skills" from "could not ask".
  getHealth(): Promise<HealthReport>;

  // What launch did about the backend. Only main knows where it looked.
  getBackendStatus(): Promise<BackendReport>;

  // Opens a folder picker for FRIDAY_CORE, remembers the choice, and retries
  // the backend. Resolves to the resulting state, unchanged if declined.
  locateBackend(): Promise<BackendReport>;

  // The operator's own biography (config/about_me.md), which the backend
  // folds into every system prompt. Read and written by main rather than
  // over HTTP — the server has no authentication, and a write endpoint that
  // persists text to disk is surface it does not need.
  readProfile(): Promise<string>;
  writeProfile(text: string): Promise<boolean>;
}
