import type { ListeningState } from "../hooks/useAlwaysListening";

// The always-listening switch. Every state is spelled out for the same reason
// MicButton spells its own out: this one holds the microphone open indefinitely,
// and an operator has to be able to tell at a glance whether it is open. A
// control that silently means "the mic is live" is the wrong control for this.
const LABELS: Record<ListeningState["kind"], { text: string; title: string }> = {
  off: {
    text: "Wake word",
    title: "Listen continuously and act when you say 'Friday'. The microphone stays open.",
  },
  starting: { text: "Opening…", title: "Waiting for the microphone" },
  waiting: {
    text: "Awake",
    title: "Listening. Say 'Friday' followed by what you want. Click to stop listening.",
  },
  hearing: { text: "Hearing…", title: "Someone is speaking. Click to stop listening." },
  denied: { text: "No mic", title: "The microphone was refused or none is connected" },
  unsupported: { text: "No mic", title: "This runtime has no microphone API" },
};

export function ListenToggle({
  state,
  onToggle,
  disabled,
}: {
  state: ListeningState;
  onToggle: () => void;
  disabled: boolean;
}) {
  const broken = state.kind === "denied" || state.kind === "unsupported";
  const live = state.kind === "waiting" || state.kind === "hearing";
  const label = LABELS[state.kind];

  return (
    <button
      type="button"
      className={
        "command__button command__button--wake"
        + (live ? " command__button--wake-live" : "")
        + (state.kind === "hearing" ? " command__button--wake-hearing" : "")
      }
      disabled={disabled || broken}
      title={label.title}
      // aria-pressed rather than a checkbox role: it is a toggle button, and
      // screen readers should announce whether the microphone is currently open.
      aria-pressed={live}
      onClick={onToggle}
    >
      <span className="command__wake-dot" aria-hidden="true" />
      {label.text}
    </button>
  );
}
