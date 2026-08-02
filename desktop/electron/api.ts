// Shape of the bridge exposed on window.friday. Kept in its own file, with
// no Node or DOM specific imports, so both electron/preload.ts (which
// implements it) and src/window.d.ts (which declares it as a global) can
// import the type without pulling either side's lib set into the other's
// tsconfig.
export interface FridayApi {
  // The renderer cannot make its own window click-through — only the
  // BrowserWindow that owns it can. This is why the toggle is a bridged
  // call into main rather than something the renderer does directly.
  setIgnoreMouseEvents(ignore: boolean, options?: { forward: boolean }): void;
}
