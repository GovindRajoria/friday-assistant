import { microphoneIsOpen, voiceStatus, type VoiceStatus } from "../voice";
import type { ListeningState } from "../hooks/useAlwaysListening";
import type { MicState } from "../hooks/useMicrophone";

// Every state is spelled out rather than collapsed into on/off. This control
// can leave the microphone open indefinitely, so an operator has to be able to
// tell at a glance whether it is open — a switch that silently means "the mic
// is live" is the wrong switch for this.
const LABELS: Record<VoiceStatus, { text: string; title: string }> = {
  off: {
    text: "Voice",
    title: "Start listening. Say 'Friday' and then what you want — it runs straight away. "
      + "For a single request without leaving the microphone open, press Ctrl+Shift+Space.",
  },
  starting: { text: "Opening…", title: "Waiting for the microphone" },
  waiting: {
    text: "Listening",
    title: "The microphone is open. Say 'Friday' and then what you want. Click to stop listening.",
  },
  hearing: { text: "Hearing you…", title: "Picking up speech now. Click to stop listening." },
  recording: { text: "Recording…", title: "Recording one request (Ctrl+Shift+Space to stop)" },
  working: { text: "Working…", title: "Running your last request. Press Stop to end it." },
  unavailable: { text: "No mic", title: "The microphone was refused, or none is connected" },
};

export function VoiceControl({
  listening,
  mic,
  busy,
  onToggle,
  disabled,
}: {
  listening: ListeningState;
  mic: MicState;
  busy: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  const status = voiceStatus(listening, mic, busy);
  const open = microphoneIsOpen(listening, mic);
  const label = LABELS[status];

  return (
    <button
      type="button"
      className={
        "command__button command__button--voice"
        + (open ? " command__button--voice-live" : "")
        + (status === "hearing" ? " command__button--voice-hearing" : "")
        + (status === "recording" ? " command__button--voice-recording" : "")
      }
      // A dead microphone leaves the button disabled rather than hidden: an
      // absent control reads as a missing feature, and this one is missing a
      // permission, which is something the operator can fix.
      disabled={disabled || status === "unavailable"}
      title={
        status === "unavailable" ? label.title
          : `${label.title} Speech is transcribed on this machine.`
      }
      // aria-pressed reports whether the microphone is open, not whether the
      // label happens to be showing a listening state — during a turn it says
      // "Working…" while the microphone is still very much open, and that is
      // the fact a screen reader has to convey.
      aria-pressed={open}
      onClick={onToggle}
    >
      <span className="command__voice-dot" aria-hidden="true" />
      {label.text}
    </button>
  );
}
