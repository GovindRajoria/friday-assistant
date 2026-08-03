import type { MicState } from "../hooks/useMicrophone";

// Every state is spelled out rather than collapsed into on/off. "Opening" in
// particular has to be visible: it is the gap between the press and the
// microphone actually being live, and an operator who starts talking during it
// loses the first word of the sentence.
const LABELS: Record<MicState["kind"], { text: string; title: string }> = {
  idle: { text: "Speak", title: "Record a request (Ctrl+Shift+Space)" },
  opening: { text: "Opening…", title: "Waiting for the microphone" },
  recording: { text: "Listening", title: "Stop recording (Ctrl+Shift+Space)" },
  denied: { text: "No mic", title: "The microphone was refused or none is connected" },
  unsupported: { text: "No mic", title: "This runtime has no microphone API" },
};

export function MicButton({
  state,
  onStart,
  onStop,
  disabled,
}: {
  state: MicState;
  onStart: () => void;
  onStop: () => void;
  disabled: boolean;
}) {
  const recording = state.kind === "recording" || state.kind === "opening";
  const broken = state.kind === "denied" || state.kind === "unsupported";
  const label = LABELS[state.kind];

  return (
    <button
      type="button"
      className={`command__button command__button--mic${recording ? " command__button--mic-live" : ""}`}
      // A dead microphone leaves the button disabled rather than hidden: an
      // absent control reads as a missing feature, and this one is missing a
      // permission, which is something the operator can fix.
      disabled={disabled || broken}
      title={broken ? label.title : `${label.title}. Speech is transcribed on this machine.`}
      aria-pressed={state.kind === "recording"}
      onClick={() => (recording ? onStop() : onStart())}
    >
      <span className="command__mic-dot" aria-hidden="true" />
      {label.text}
    </button>
  );
}
