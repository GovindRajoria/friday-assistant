// What the one voice control is showing, derived from the three things that
// can be true at once.
//
// There used to be two buttons on the command bar — "Speak" for push-to-talk
// and "Wake word" for continuous listening — and the operator's verdict was
// that the arrangement itself was the problem. Two controls for one capability
// makes the operator decide which kind of microphone they want before they
// have said anything, which is a question about this program's internals
// dressed up as a question about their intent.
//
// So there is one control now, and this is where its state is worked out.
// Deriving it in a pure function rather than inside the component is the same
// choice reducer.ts makes: the precedence below is a set of judgements about
// what matters most to see, and judgements are worth being able to assert.
import type { ListeningState } from "./hooks/useAlwaysListening";
import type { MicState } from "./hooks/useMicrophone";

export type VoiceStatus =
  // No microphone is open. The resting state, and the default on first run.
  | "off"
  // Waiting for the operating system to hand over the device.
  | "starting"
  // Open, nobody talking. Listening for the wake word.
  | "waiting"
  // Somebody is talking into an open microphone right now.
  | "hearing"
  // A one-shot recording from the global hotkey is in progress.
  | "recording"
  // A turn is running. Shown in preference to the listening states below it,
  // because it is the answer to "did it hear me" and they are not.
  | "working"
  // The device was refused, or there is none, or the runtime has no API for
  // it. Three different causes, one thing to do about it: look at the title.
  | "unavailable";

export function voiceStatus(
  listening: ListeningState,
  mic: MicState,
  busy: boolean,
): VoiceStatus {
  // Broken first, and from either mode: a control that shows "Listening" while
  // the device is refused is the one failure that wastes the operator's time
  // in silence, because there is nothing on screen to suggest they should stop
  // talking.
  if (
    listening.kind === "denied" || listening.kind === "unsupported"
    || mic.kind === "denied" || mic.kind === "unsupported"
  ) {
    return "unavailable";
  }
  // A deliberate press outranks everything: it is a thing the operator just
  // did, and they need to see that it took.
  if (mic.kind === "recording" || mic.kind === "opening") return "recording";
  // Ahead of `hearing` on purpose. While an answer is being spoken the open
  // microphone really is picking up sound — its own voice, out of a speaker —
  // and labelling that "Hearing…" would read as though the assistant had
  // mistaken itself for the operator, which is alarming rather than
  // informative. What they want to know at that moment is that it is working.
  if (busy) return "working";
  if (listening.kind === "hearing") return "hearing";
  if (listening.kind === "waiting") return "waiting";
  if (listening.kind === "starting") return "starting";
  return "off";
}

// Is the microphone open, in either mode? Drives the live styling, separately
// from the label — during a turn the label says "Working" while the dot must
// still show that the room is being listened to.
export function microphoneIsOpen(listening: ListeningState, mic: MicState): boolean {
  return listening.kind === "waiting" || listening.kind === "hearing"
    || mic.kind === "recording" || mic.kind === "opening";
}
