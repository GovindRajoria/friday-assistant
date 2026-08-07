// The pure event-handling core of the HUD.
//
// Kept apart from src/useAgentSocket.ts on purpose: this file touches
// neither WebSocket nor timers, so every transition can be asserted with a
// plain function call in a test, no fake socket or fake clock required.
// useAgentSocket owns the side effects (connecting, reconnecting, deciding
// what id to hand each event) and dispatches into this reducer; nothing in
// here reaches back out to a socket.
import type { ActionPayload, AgentEvent, AgentEventType } from "./events";

// "speaking" is declared but never produced by orbForEvent below. The
// backend has no speech-completion event, so nothing could move the orb out
// of it; it stays in the union as the state to use once one exists.
export type OrbState = "idle" | "thinking" | "speaking" | "error";

export interface TranscriptEntry {
  id: string;
  type: AgentEventType;
  text: string;
}

export interface HudState {
  orb: OrbState;
  connected: boolean;
  transcript: TranscriptEntry[];
  // Latest ambient screen description (Phase 4). Kept apart from
  // `transcript` on purpose — it replaces itself as the desktop changes
  // rather than accumulating, so a busy screen does not fill the
  // scrolling transcript with lines nobody asked for.
  screenContext: string;
  // The destructive action currently awaiting a human answer, or null when
  // nothing is pending (Phase 6). Cleared on "confirmation_resolved" (the
  // approve/deny buttons were clicked) and on every terminal event, because
  // the backend's 60s timeout can end a turn without this client ever
  // clicking anything — a stale prompt left rendered after that would offer
  // to approve or deny a question nobody is waiting on anymore.
  pendingConfirmation: ActionPayload | null;
}

export const initialState: HudState = {
  orb: "idle",
  connected: false,
  transcript: [],
  screenContext: "",
  pendingConfirmation: null,
};

export type HudAction =
  | { kind: "connected" }
  | { kind: "disconnected" }
  | { kind: "prompt_sent" }
  // `id` is supplied by the caller rather than generated here (e.g. with
  // crypto.randomUUID()) — a reducer that reaches for its own source of
  // entropy is no longer a pure function of (state, action).
  | { kind: "agent_event"; event: AgentEvent; id: string }
  // Dispatched by useAgentSocket.sendConfirm the moment a button is
  // clicked, independent of any event arriving back over the socket — the
  // HUD should not keep showing a resolved prompt while waiting for the
  // network round trip.
  | { kind: "confirmation_resolved" };

// action's and confirmation_required's payload shape ({name, input}) differs
// from every other event type ({text}) — the one discriminated-union case in
// this file, and the one place a naive `.text` access would render
// `undefined`.
function describe(event: AgentEvent): string {
  if (event.type === "action" || event.type === "confirmation_required") {
    return `${event.payload.name}(${JSON.stringify(event.payload.input)})`;
  }
  return event.payload.text;
}

function orbForEvent(type: AgentEventType, previous: OrbState): OrbState {
  switch (type) {
    case "thought":
    case "action":
    case "observation":
    case "confirmation_required":
      return "thinking";
    case "answer":
    case "status":
      // Both end a turn, so the orb goes back to idle. It does not go to
      // "speaking": the backend hands narration to a fire-and-forget speech
      // thread and emits nothing when that finishes, so there is no event
      // that could ever move the orb out of it again. Showing a state we
      // cannot observe the end of leaves the HUD stuck claiming the agent is
      // still talking long after it stopped.
      return "idle";
    case "anomaly":
    case "error":
      return "error";
    case "proactive":
      // Nothing asked for this, so nothing about the turn changed. Moving the
      // orb here would show a reminder as though the agent had started
      // thinking, and it must not clear a pending confirmation either — the
      // backend is still blocked waiting on that answer.
      return previous;
    case "transcript":
      // Being heard IS being asked, as of the auto-submit change: the backend
      // emits this immediately before running the turn. Moving the orb here
      // rather than waiting for the first `thought` is what makes a spoken
      // request look answered-in-progress straight away, which matters most in
      // the mode where nobody is looking at the prompt box.
      //
      // Safe against the one path that emits this and then stops — a turn
      // rejected by single-flight — because that rejection is a `status`, and
      // `status` returns the orb to idle.
      return "thinking";
    default:
      // screen_context lands here too, though in practice the reducer
      // never calls this function for it — it is intercepted earlier in
      // the "agent_event" case below and routed to its own field instead,
      // specifically so it does not affect orb state.
      return previous;
  }
}

export function reducer(state: HudState, action: HudAction): HudState {
  switch (action.kind) {
    case "connected":
      // The orb is reset, not just the flag. "disconnected" below moves the
      // orb to "error", and nothing else ever moved it back — so a socket
      // that dropped and reconnected left the HUD showing a fault next to a
      // status bar reporting a live link. Seen on every launch, because
      // React StrictMode mounts the socket effect twice: connect, tear down
      // (error), reconnect. A reconnect also means any turn that was in
      // flight is gone, so idle is the honest state either way.
      return { ...state, connected: true, orb: "idle" };
    case "disconnected":
      // A dropped socket mid-turn should read as an error, not as if the
      // agent quietly finished — idle would look identical to "done".
      return { ...state, connected: false, orb: "error" };
    case "prompt_sent":
      return { ...state, orb: "thinking" };
    case "agent_event": {
      if (action.event.type === "screen_context") {
        // Ambient background, not a step in the turn — updates the
        // standalone field rather than appending to the transcript, and
        // deliberately does not touch orb (see orbForEvent's own comment).
        return { ...state, screenContext: action.event.payload.text };
      }
      const entry: TranscriptEntry = {
        id: action.id,
        type: action.event.type,
        text: describe(action.event),
      };
      const next: HudState = {
        ...state,
        orb: orbForEvent(action.event.type, state.orb),
        transcript: [...state.transcript, entry],
      };
      if (action.event.type === "confirmation_required") {
        return { ...next, pendingConfirmation: action.event.payload };
      }
      if (action.event.type === "answer" || action.event.type === "error") {
        // Covers the "server gave up" path: a 60s unanswered confirmation
        // times out to a denial and the turn moves on to an answer without
        // this client ever dispatching confirmation_resolved.
        return { ...next, pendingConfirmation: null };
      }
      return next;
    }
    case "confirmation_resolved":
      return { ...state, pendingConfirmation: null };
    default:
      return state;
  }
}
