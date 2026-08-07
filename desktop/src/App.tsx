import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConfirmationPrompt } from "./components/ConfirmationPrompt";
import { ProfileEditor } from "./components/ProfileEditor";
import { PromptInput } from "./components/PromptInput";
import { Reactor } from "./components/Reactor";
import { ScreenContext } from "./components/ScreenContext";
import { StatusBar } from "./components/StatusBar";
import { SystemsPanel } from "./components/SystemsPanel";
import { TitleBar } from "./components/TitleBar";
import { Transcript } from "./components/Transcript";
import { VoiceControl } from "./components/VoiceControl";
import { useAgentSocket } from "./hooks/useAgentSocket";
import { useHealth } from "./hooks/useHealth";
import { useAlwaysListening } from "./hooks/useAlwaysListening";
import { useMicrophone } from "./hooks/useMicrophone";
import "./App.css";

// Whether the microphone was left open last time. Persisted so a restart does
// not silently drop the operator back to a dead command bar after they chose
// hands-free — but stored rather than defaulted: absent means off, so the very
// first run of a fresh install never opens the microphone on its own. That is
// the same argument useAlwaysListening.ts makes in its header, and this is the
// line where it would have been easiest to quietly reverse.
const LISTENING_PREFERENCE = "friday.listening";

export function App() {
  const { state, sendPrompt, sendAudio, sendListenMode, sendCancel, sendConfirm } = useAgentSocket();
  const { health, refresh: refreshHealth } = useHealth();
  const { micState, startRecording, stopRecording } = useMicrophone(sendAudio);
  const { listeningState, startListening, stopListening } = useAlwaysListening(sendAudio);
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

  const listening = listeningState.kind !== "off"
    && listeningState.kind !== "denied"
    && listeningState.kind !== "unsupported";

  const toggleRecording = useCallback(() => {
    if (micState.kind === "recording" || micState.kind === "opening") stopRecording();
    else startRecording();
  }, [micState.kind, startRecording, stopRecording]);

  // The two microphone modes are mutually exclusive, and that is enforced here
  // rather than trusted: two MediaRecorders on two getUserMedia streams would
  // both ship overlapping audio, and the backend would transcribe the same
  // sentence twice and run it twice.
  const toggleAlwaysListening = useCallback(() => {
    if (listening) {
      stopListening();
      sendListenMode(false);
      window.localStorage.removeItem(LISTENING_PREFERENCE);
      return;
    }
    if (micState.kind === "recording" || micState.kind === "opening") stopRecording();
    // The mode is announced before the first frame can arrive, so no utterance
    // is ever read under the wrong interpretation.
    sendListenMode(true);
    startListening();
    window.localStorage.setItem(LISTENING_PREFERENCE, "on");
  }, [listening, micState.kind, sendListenMode, startListening, stopListening, stopRecording]);

  // Reopen the microphone on launch if that is where it was left. Gated on the
  // socket being up, because sendListenMode on a dead socket is dropped and the
  // backend would then read the frames that follow as push-to-talk recordings —
  // no wake word, every overheard sentence run as a request. The ref makes it
  // once per connection rather than once per render.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current || !state.connected) return;
    restoredRef.current = true;
    if (window.localStorage.getItem(LISTENING_PREFERENCE) !== "on") return;
    sendListenMode(true);
    startListening();
  }, [state.connected, sendListenMode, startListening]);

  // If the socket drops, tell the backend again when it returns: listen mode is
  // per-connection state there, so a reconnect silently reverts it to
  // push-to-talk while this window still believes it is listening.
  useEffect(() => {
    if (state.connected && listening) sendListenMode(true);
  }, [state.connected, listening, sendListenMode]);

  // The global hotkey is registered by the main process — a renderer key
  // handler only fires when this window already has focus, which is the one
  // case where reaching for the button was never the problem. Main forwards
  // the press here; the recording itself stays in the renderer, because that
  // is where the microphone is.
  useEffect(() => window.friday?.onToggleDictation?.(toggleRecording), [toggleRecording]);

  // The same combination again, handled in the renderer, which fires only when
  // this window already has focus. That used to be the one case where reaching
  // for the button was never the problem — but the button is gone, so if another
  // application holds the global registration (main.ts reports that and carries
  // on) push-to-talk would have had no way in at all. This is that way in.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.ctrlKey || !event.shiftKey || event.code !== "Space") return;
      event.preventDefault();
      toggleRecording();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggleRecording]);

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
          <Reactor
            state={state.orb}
            connected={state.connected}
            listening={micState.kind === "recording" || listeningState.kind === "hearing"}
          />
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
        >
          {/* One control for the whole capability. Push-to-talk kept its global
              hotkey and lost its button — see src/voice.ts for why two was the
              wrong number. */}
          <VoiceControl
            listening={listeningState}
            mic={micState}
            busy={state.orb === "thinking"}
            onToggle={toggleAlwaysListening}
            disabled={!state.connected}
          />
        </PromptInput>
      </footer>
    </div>
  );
}
