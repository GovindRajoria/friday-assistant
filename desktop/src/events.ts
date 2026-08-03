// Event vocabulary streamed by the backend over /ws.
//
// This union is the renderer's side of a contract whose other half is
// server/events.py's ALL_TYPES. Nothing wires the two together at build
// time — FRIDAY_CORE/tests/test_event_vocabulary_drift.py reads this file
// with a regex and asserts set equality against events.ALL_TYPES, so a type
// added to one side and forgotten on the other fails a Python test rather
// than failing silently at runtime in a build the Node CI job would not
// catch either. Keep this as a single flat union literal — the drift gate
// parses it as one block of quoted strings.
export type AgentEventType =
  | "thought"
  | "action"
  | "observation"
  | "answer"
  | "anomaly"
  | "error"
  | "status"
  | "screen_context"
  | "confirmation_required"
  | "proactive"
  | "transcript";

export interface TextPayload {
  text: string;
}

export interface ActionPayload {
  name: string;
  input: Record<string, unknown>;
}

// screen_context has no consumer yet — Phase 4 ambient VLM description,
// nothing on the wire emits it today (server/events.py's own docstring says
// so). It is in the union because the drift gate requires this file to
// match ALL_TYPES exactly, not because anything here renders it.
//
// confirmation_required (Phase 6) reuses ActionPayload rather than
// TextPayload — the HUD needs the actual proposed {name, input} to show a
// human before a destructive skill runs, not just a sentence about it.
export type AgentEvent =
  | { type: "thought"; payload: TextPayload }
  | { type: "action"; payload: ActionPayload }
  | { type: "observation"; payload: TextPayload }
  | { type: "answer"; payload: TextPayload }
  | { type: "anomaly"; payload: TextPayload }
  | { type: "error"; payload: TextPayload }
  | { type: "status"; payload: TextPayload }
  | { type: "screen_context"; payload: TextPayload }
  | { type: "confirmation_required"; payload: ActionPayload }
  | { type: "proactive"; payload: TextPayload }
  | { type: "transcript"; payload: TextPayload };

export function isAgentEvent(value: unknown): value is AgentEvent {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as { type?: unknown; payload?: unknown };
  return typeof candidate.type === "string" && typeof candidate.payload === "object" && candidate.payload !== null;
}
