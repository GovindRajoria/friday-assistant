import { useCallback, useEffect } from "react";
import { Orb } from "./components/Orb";
import { PromptInput } from "./components/PromptInput";
import { ScreenContext } from "./components/ScreenContext";
import { Transcript } from "./components/Transcript";
import { useAgentSocket } from "./hooks/useAgentSocket";
import "./App.css";

export function App() {
  const { state, sendPrompt, sendCancel } = useAgentSocket();

  // The window starts fully click-through — a frameless, always-on-top HUD
  // that swallowed every click on the desktop underneath it would be
  // unusable as an overlay. It only becomes interactive while the pointer
  // is actually over the HUD, toggled below.
  //
  // window.friday is optional-chained rather than called directly. Verified
  // live: a broken preload (an ESM/CommonJS bundle mismatch, since fixed in
  // electron.vite.config.ts) left it undefined, this call threw
  // uncaught inside a passive effect, and with no error boundary above it
  // React unmounted the entire tree — the HUD's own click-through wiring
  // turned into a blank window. The transcript and orb should still render
  // even if the bridge is ever missing again; losing click-through toggling
  // is a degraded HUD, not an invisible one.
  useEffect(() => {
    window.friday?.setIgnoreMouseEvents(true, { forward: true });
  }, []);

  const handleMouseEnter = useCallback(() => {
    window.friday?.setIgnoreMouseEvents(false);
  }, []);

  const handleMouseLeave = useCallback(() => {
    window.friday?.setIgnoreMouseEvents(true, { forward: true });
  }, []);

  return (
    <div className="hud" onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      {/* The only draggable surface — a frameless window with nothing
          marked -webkit-app-region: drag (set in App.css) cannot be moved
          at all. */}
      <div className="hud__drag-region" />
      <div className="hud__content">
        <Orb state={state.orb} connected={state.connected} />
        <Transcript entries={state.transcript} />
        <ScreenContext text={state.screenContext} />
        <PromptInput onSubmit={sendPrompt} onCancel={sendCancel} disabled={!state.connected} />
      </div>
    </div>
  );
}
