import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmationPrompt } from "./components/ConfirmationPrompt";
import { PromptInput } from "./components/PromptInput";
import { Reactor } from "./components/Reactor";
import { ScreenContext } from "./components/ScreenContext";
import { StatusBar } from "./components/StatusBar";
import { SystemsPanel } from "./components/SystemsPanel";
import { Transcript } from "./components/Transcript";
import { useAgentSocket } from "./hooks/useAgentSocket";
import { useHealth } from "./hooks/useHealth";
import "./App.css";

const TABS = [
  { id: "log", label: "LOG" },
  { id: "sys", label: "SYS" },
  { id: "vis", label: "VIS" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function App() {
  const { state, sendPrompt, sendCancel, sendConfirm } = useAgentSocket();
  const health = useHealth();
  // Which panel is showing is a view preference, not something that arrived
  // over the socket, so it stays out of the reducer.
  const [tab, setTab] = useState<TabId>("log");

  // The window starts fully click-through — a frameless, always-on-top HUD
  // that swallowed every click on the desktop underneath it would be
  // unusable as an overlay. It only becomes interactive while the pointer
  // is actually over the HUD, toggled below.
  //
  // This works because the chassis fills the window exactly. If the layout
  // ever grows into a larger window with transparent gaps around it, these
  // handlers have to move onto each interactive panel instead — entering
  // anywhere on an empty region would otherwise make the whole window
  // opaque to the mouse.
  //
  // window.friday is optional-chained rather than called directly. Verified
  // live: a broken preload (an ESM/CommonJS bundle mismatch, since fixed in
  // electron.vite.config.ts) left it undefined, this call threw
  // uncaught inside a passive effect, and with no error boundary above it
  // React unmounted the entire tree — the HUD's own click-through wiring
  // turned into a blank window. The transcript and reactor should still
  // render even if the bridge is ever missing again; losing click-through
  // toggling is a degraded HUD, not an invisible one.
  useEffect(() => {
    window.friday?.setIgnoreMouseEvents(true, { forward: true });
  }, []);

  const handleMouseEnter = useCallback(() => {
    window.friday?.setIgnoreMouseEvents(false);
  }, []);

  const handleMouseLeave = useCallback(() => {
    window.friday?.setIgnoreMouseEvents(true, { forward: true });
  }, []);

  // Counted off the transcript rather than tracked separately: an `answer`
  // is the terminal event of a turn, so the number of them is the number of
  // turns that finished. Nothing invented, nothing to keep in sync.
  const turns = useMemo(
    () => state.transcript.filter((entry) => entry.type === "answer").length,
    [state.transcript],
  );

  const pending = state.pendingConfirmation;

  return (
    <div className="hud" onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      <span className="hud__bracket hud__bracket--tl" aria-hidden="true" />
      <span className="hud__bracket hud__bracket--tr" aria-hidden="true" />
      <span className="hud__bracket hud__bracket--bl" aria-hidden="true" />
      <span className="hud__bracket hud__bracket--br" aria-hidden="true" />
      <span className="hud__scan" aria-hidden="true" />

      {/* The only draggable surface — a frameless window with nothing marked
          -webkit-app-region: drag (set in App.css) cannot be moved at all. */}
      <header className="hud__titlebar">
        <span className="hud__mark">FRIDAY</span>
        <span className="hud__subtitle">local agent · multimodal</span>
      </header>

      <div className="hud__body">
        <Reactor state={state.orb} connected={state.connected} />

        <nav className="tabs" aria-label="Panels">
          {TABS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              className={`tabs__tab ${tab === entry.id ? "tabs__tab--active" : ""}`}
              onClick={() => setTab(entry.id)}
              aria-pressed={tab === entry.id}
            >
              {entry.label}
            </button>
          ))}
        </nav>

        {/* A pending confirmation sits above the panel rather than replacing
            it. Replacing it hid the LOG at exactly the moment of decision —
            and the model's stated reason for wanting the action is a
            `thought` line in that log. The whole argument for keeping
            narration a first-class field (docs/DESIGN.md) is that intent is
            the feedback channel; hiding it to ask for authorisation is the
            one place that must not happen. The socket's
            `confirmation_required` payload carries only {name, input}, so
            the transcript underneath is the only place that reasoning
            appears at all. */}
        <ConfirmationPrompt
          pending={pending}
          onApprove={() => sendConfirm(true)}
          onDeny={() => sendConfirm(false)}
        />

        <section className="panel">
          {tab === "log" && <Transcript entries={state.transcript} />}
          {tab === "sys" && <SystemsPanel health={health} />}
          {tab === "vis" && <ScreenContext text={state.screenContext} />}
        </section>

        <PromptInput
          onSubmit={sendPrompt}
          onCancel={sendCancel}
          disabled={!state.connected}
          busy={state.orb === "thinking"}
        />
        <StatusBar
          connected={state.connected}
          health={health}
          turns={turns}
          events={state.transcript.length}
        />
      </div>
    </div>
  );
}
