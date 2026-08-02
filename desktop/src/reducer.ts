// The pure event-handling core of the HUD.
//
// Kept apart from src/useAgentSocket.ts on purpose: this file touches
// neither WebSocket nor timers, so every transition can be asserted with a
// plain function call in a test, no fake socket or fake clock required.
// useAgentSocket owns the side effects (connecting, reconnecting, deciding
// what id to hand each event) and dispatches into this reducer; nothing in
// here reaches back out to a socket.
import type { AgentEvent, AgentEventType } from "./events";

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
}

export const initialState: HudState = {
  orb: "idle",
  connected: false,
  transcript: [],
  screenContext: "",
};

export type HudAction =
  | { kind: "connected" }
  | { kind: "disconnected" }
  | { kind: "prompt_sent" }
  // `id` is supplied by the caller rather than generated here (e.g. with
  // crypto.randomUUID()) — a reducer that reaches for its own source of
  // entropy is no longer a pure function of (state, action).
  | { kind: "agent_event"; event: AgentEvent; id: string };

// action's payload shape ({name, input}) differs from every other event
// type ({text}) — the one discriminated-union case in this file, and the
// one place a naive `.text` access would render `undefined`.
function describe(event: AgentEvent): string {
  if (event.type === "action") {
    return `${event.payload.name}(${JSON.stringify(event.payload.input)})`;
  }
  return event.payload.text;
}

function orbForEvent(type: AgentEventType, previous: OrbState): OrbState {
  switch (type) {
    case "thought":
    case "action":
    case "observation":
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
      return { ...state, connected: true };
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
      return {
        ...state,
        orb: orbForEvent(action.event.type, state.orb),
        transcript: [...state.transcript, entry],
      };
    }
    default:
      return state;
  }
}
