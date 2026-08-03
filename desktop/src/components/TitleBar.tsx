import { useCallback, useState } from "react";

// The window is frameless, so this is the frame.
//
// It is the only surface marked -webkit-app-region: drag (in App.css), and
// every control inside it has to opt back out with no-drag or the OS takes
// the click as the start of a window move and the button never fires.
export function TitleBar() {
  // Mirrored from what main reports after the call, not set optimistically —
  // the window is the authority on whether it is actually on top. Local
  // state rather than the reducer: nothing about pinning arrived over the
  // socket.
  const [pinned, setPinned] = useState(false);

  // Every bridge call is optional-chained. A broken preload has already
  // unmounted this whole tree once in this project, by throwing inside an
  // effect with no error boundary above it; losing the window controls is a
  // degraded HUD, an invisible one is not.
  const togglePin = useCallback(async () => {
    const nowPinned = await window.friday?.toggleAlwaysOnTop();
    setPinned(Boolean(nowPinned));
  }, []);

  return (
    <header className="titlebar">
      <div className="titlebar__identity">
        <span className="titlebar__mark">FRIDAY</span>
        <span className="titlebar__subtitle">local agent · multimodal</span>
      </div>

      <div className="titlebar__controls">
        <button
          type="button"
          className={`titlebar__pin ${pinned ? "titlebar__pin--on" : ""}`}
          onClick={togglePin}
          title={pinned ? "Keep on top: on" : "Keep on top: off"}
          aria-pressed={pinned}
        >
          {pinned ? "PINNED" : "PIN"}
        </button>
        <button
          type="button"
          className="titlebar__button"
          onClick={() => window.friday?.minimize()}
          aria-label="Minimise"
        >
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path d="M2 6h8" />
          </svg>
        </button>
        <button
          type="button"
          className="titlebar__button"
          onClick={() => window.friday?.toggleMaximize()}
          aria-label="Maximise or restore"
        >
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <rect x="2.5" y="2.5" width="7" height="7" />
          </svg>
        </button>
        <button
          type="button"
          className="titlebar__button titlebar__button--close"
          onClick={() => window.friday?.close()}
          aria-label="Close"
        >
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path d="M3 3l6 6M9 3l-6 6" />
          </svg>
        </button>
      </div>
    </header>
  );
}
