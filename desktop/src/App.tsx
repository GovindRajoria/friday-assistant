import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmationPrompt } from "./components/ConfirmationPrompt";
import { MicButton } from "./components/MicButton";
import { ProfileEditor } from "./components/ProfileEditor";
import { PromptInput } from "./components/PromptInput";
import { Reactor } from "./components/Reactor";
import { ScreenContext } from "./components/ScreenContext";
import { StatusBar } from "./components/StatusBar";
import { SystemsPanel } from "./components/SystemsPanel";
import { TitleBar } from "./components/TitleBar";
import { Transcript } from "./components/Transcript";
import { useAgentSocket } from "./hooks/useAgentSocket";
import { useHealth } from "./hooks/useHealth";
import { useMicrophone } from "./hooks/useMicrophone";
import "./App.css";

export function App() {
  const { state, sendPrompt, sendAudio, sendCancel, sendConfirm } = useAgentSocket();
  const { health, refresh: refreshHealth } = useHealth();
  const { micState, startRecording, stopRecording } = useMicrophone(sendAudio);
  // Whether the biography editor is open is a view preference, not
  // something the backend said, so it stays out of the reducer.
  const [editingProfile, setEditingProfile] = useState(false);

  // Counted off the transcript rather than tracked separately: an `answer`
  // is the terminal event of a turn, so the number of them is the number of
  // turns that finished. Nothing invented, nothing to keep in sync.
  const turns = useMemo(
    () => state.transcript.filter((entry) => entry.type === "answer").length,
    [state.transcript],
  );

  const toggleRecording = useCallback(() => {
    if (micState.kind === "recording" || micState.kind === "opening") stopRecording();
    else startRecording();
  }, [micState.kind, startRecording, stopRecording]);

  // The global hotkey is registered by the main process — a renderer key
  // handler only fires when this window already has focus, which is the one
  // case where reaching for the button was never the problem. Main forwards
  // the press here; the recording itself stays in the renderer, because that
  // is where the microphone is.
  useEffect(() => window.friday?.onToggleDictation?.(toggleRecording), [toggleRecording]);

  return (
    <div className="hud">
      <span className="hud__scan" aria-hidden="true" />
      <TitleBar onEditProfile={() => setEditingProfile(true)} />
      {editingProfile && <ProfileEditor onClose={() => setEditingProfile(false)} />}

      {/* Three columns rather than one narrow tabbed stack. The window is a
          desktop window now, so everything the HUD knows fits on screen at
          once and nothing has to be switched to. */}
      <main className="hud__body">
        <aside className="rail rail--left">
          {/* Recording is a local fact about this window, not something the
              backend reported, so it is passed in beside the orb state rather
              than folded into the reducer. */}
          <Reactor state={state.orb} connected={state.connected} listening={micState.kind === "recording"} />
          <StatusBar connected={state.connected} health={health} turns={turns} events={state.transcript.length} />
        </aside>

        <section className="column column--main">
          {/* Above the transcript, never replacing it. The socket's
              confirmation_required payload carries only {name, input}; the
              model's stated reason for wanting the action is a thought line
              in the log underneath, and hiding that at the moment of
              decision is the one place this project cannot afford to. */}
          <ConfirmationPrompt
            pending={state.pendingConfirmation}
            onApprove={() => sendConfirm(true)}
            onDeny={() => sendConfirm(false)}
          />
          <Transcript entries={state.transcript} />
        </section>

        <aside className="rail rail--right">
          <div className="rail__section">
            <h2 className="rail__heading">Systems</h2>
            <SystemsPanel health={health} onRefresh={refreshHealth} />
          </div>
          <div className="rail__section rail__section--vision">
            <h2 className="rail__heading">Screen</h2>
            <ScreenContext text={state.screenContext} />
          </div>
        </aside>
      </main>

      <footer className="hud__footer">
        <PromptInput
          onSubmit={sendPrompt}
          onCancel={sendCancel}
          disabled={!state.connected}
          busy={state.orb === "thinking"}
          dictation={state.dictation}
        >
          <MicButton
            state={micState}
            onStart={startRecording}
            onStop={stopRecording}
            disabled={!state.connected}
          />
        </PromptInput>
      </footer>
    </div>
  );
}
