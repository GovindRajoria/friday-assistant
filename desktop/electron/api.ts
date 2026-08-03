// Shape of the bridge exposed on window.friday. Kept in its own file, with
// no Node or DOM specific imports, so both electron/preload.ts (which
// implements it) and src/window.d.ts (which declares it as a global) can
// import the type without pulling either side's lib set into the other's
// tsconfig.
export interface HealthReport {
  status: string;
  skills: string[];
}

export interface FridayApi {
  // The renderer cannot make its own window click-through — only the
  // BrowserWindow that owns it can. This is why the toggle is a bridged
  // call into main rather than something the renderer does directly.
  setIgnoreMouseEvents(ignore: boolean, options?: { forward: boolean }): void;

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
}
