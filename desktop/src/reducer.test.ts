import { describe, expect, it } from "vitest";
import { initialState, reducer } from "./reducer";
import type { AgentEvent } from "./events";

function agentEvent(event: AgentEvent, id = "id-1") {
  return reducer(initialState, { kind: "agent_event", event, id });
}

describe("reducer", () => {
  it("starts idle and disconnected", () => {
    expect(initialState).toEqual({
      orb: "idle",
      connected: false,
      transcript: [],
      screenContext: "",
      pendingConfirmation: null,
    });
  });

  it("marks connected on a connected action", () => {
    const state = reducer(initialState, { kind: "connected" });
    expect(state.connected).toBe(true);
  });

  it("clears a stale fault when the socket comes back", () => {
    // Regression for a defect visible on every single launch: StrictMode
    // mounts the socket effect twice, so the first teardown dispatched
    // "disconnected" (orb: error) and the reconnect only set the flag back.
    // The HUD then showed a red reactor labelled Fault directly above a
    // status bar reporting a live link.
    const dropped = reducer(initialState, { kind: "disconnected" });
    expect(dropped.orb).toBe("error");

    const back = reducer(dropped, { kind: "connected" });
    expect(back.connected).toBe(true);
    expect(back.orb).toBe("idle");
  });

  it("treats a dropped connection as an error, not idle", () => {
    const connected = reducer(initialState, { kind: "connected" });
    const state = reducer(connected, { kind: "disconnected" });
    expect(state.connected).toBe(false);
    expect(state.orb).toBe("error");
  });

  it("moves the orb to thinking as soon as a prompt is sent", () => {
    const state = reducer(initialState, { kind: "prompt_sent" });
    expect(state.orb).toBe("thinking");
  });

  it.each([
    ["thought", "thinking"],
    ["observation", "thinking"],
    // Terminal events return the orb to idle; see orbForEvent on why
    // neither goes to "speaking".
    ["answer", "idle"],
    ["status", "idle"],
    ["anomaly", "error"],
    ["error", "error"],
  ] as const)("routes a %s event to orb state %s", (type, expected) => {
    const state = agentEvent({ type, payload: { text: "hello" } });
    expect(state.orb).toBe(expected);
  });

  it("leaves the orb untouched on a screen_context event", () => {
    const thinking = reducer(initialState, { kind: "prompt_sent" });
    const state = reducer(thinking, {
      kind: "agent_event",
      event: { type: "screen_context", payload: { text: "a desk with two monitors" } },
      id: "id-2",
    });
    expect(state.orb).toBe("thinking");
  });

  it("stores a screen_context event in its own field, not the transcript", () => {
    const state = agentEvent({ type: "screen_context", payload: { text: "a desk with two monitors" } });
    expect(state.screenContext).toBe("a desk with two monitors");
    expect(state.transcript).toEqual([]);
  });

  it("replaces the previous screen_context rather than accumulating", () => {
    const first = agentEvent({ type: "screen_context", payload: { text: "one monitor" } });
    const second = reducer(first, {
      kind: "agent_event",
      event: { type: "screen_context", payload: { text: "two monitors" } },
      id: "id-2",
    });
    expect(second.screenContext).toBe("two monitors");
    expect(second.transcript).toEqual([]);
  });

  it("renders a plain text event's payload verbatim", () => {
    const state = agentEvent({ type: "thought", payload: { text: "checking the weather" } });
    expect(state.transcript).toEqual([{ id: "id-1", type: "thought", text: "checking the weather" }]);
  });

  it("formats an action event's {name, input} shape, the one payload that isn't {text}", () => {
    const state = agentEvent({
      type: "action",
      payload: { name: "check_weather", input: { city: "Mumbai" } },
    });
    expect(state.transcript[0].text).toBe('check_weather({"city":"Mumbai"})');
  });

  it("appends to the transcript rather than replacing it", () => {
    const first = agentEvent({ type: "thought", payload: { text: "one" } });
    const second = reducer(first, {
      kind: "agent_event",
      event: { type: "observation", payload: { text: "two" } },
      id: "id-2",
    });
    expect(second.transcript.map((entry) => entry.text)).toEqual(["one", "two"]);
  });

  it("holds a confirmation_required event as the pending confirmation", () => {
    const state = agentEvent({
      type: "confirmation_required",
      payload: { name: "manage_files", input: { action: "delete", path: "scratch.txt" } },
    });
    expect(state.pendingConfirmation).toEqual({
      name: "manage_files",
      input: { action: "delete", path: "scratch.txt" },
    });
    // It is also an ordinary transcript line, rendered the same way an
    // `action` is — the operator should still be able to scroll back and see
    // what they were asked about after the prompt itself is gone.
    expect(state.transcript[0].text).toBe('manage_files({"action":"delete","path":"scratch.txt"})');
    expect(state.orb).toBe("thinking");
  });

  it("clears the pending confirmation when the buttons are clicked", () => {
    const asked = agentEvent({
      type: "confirmation_required",
      payload: { name: "manage_files", input: {} },
    });
    const resolved = reducer(asked, { kind: "confirmation_resolved" });
    expect(resolved.pendingConfirmation).toBeNull();
  });

  it("clears the pending confirmation on a terminal event nobody answered", () => {
    // The server's 60s timeout denies on its own and the turn moves on to an
    // answer without this client ever clicking anything. A prompt left
    // rendered after that would offer to approve a question nothing is
    // waiting on — the click would go nowhere.
    const asked = agentEvent({
      type: "confirmation_required",
      payload: { name: "manage_files", input: {} },
    });
    const answered = reducer(asked, {
      kind: "agent_event",
      event: { type: "answer", payload: { text: "nothing was done" } },
      id: "id-2",
    });
    expect(answered.pendingConfirmation).toBeNull();

    const errored = reducer(asked, {
      kind: "agent_event",
      event: { type: "error", payload: { text: "the turn failed" } },
      id: "id-3",
    });
    expect(errored.pendingConfirmation).toBeNull();
  });

  it("leaves the orb alone for an unprompted message", () => {
    // A reminder or briefing is not a turn. Moving the orb would show it as
    // though the agent had started thinking about something nobody asked.
    const thinking = reducer(initialState, { kind: "prompt_sent" });
    const during = reducer(thinking, {
      kind: "agent_event",
      event: { type: "proactive", payload: { text: "Reminder: stand up" } },
      id: "id-9",
    });
    expect(during.orb).toBe("thinking");

    const whenIdle = agentEvent({ type: "proactive", payload: { text: "Reminder: stand up" } });
    expect(whenIdle.orb).toBe("idle");
    expect(whenIdle.transcript[0].text).toBe("Reminder: stand up");
  });

  it("does not clear a pending confirmation when something unprompted arrives", () => {
    // The backend is still blocked waiting on that answer. Clearing the
    // prompt because a reminder fired would strand the turn with no way to
    // answer it short of the sixty-second timeout.
    const asked = agentEvent({
      type: "confirmation_required",
      payload: { name: "manage_files", input: {} },
    });
    const interrupted = reducer(asked, {
      kind: "agent_event",
      event: { type: "proactive", payload: { text: "Reminder: stand up" } },
      id: "id-10",
    });
    expect(interrupted.pendingConfirmation).toEqual({ name: "manage_files", input: {} });
  });

  it("shows a heard sentence as a turn already starting", () => {
    // Replaces an assertion that this event did NOT start a turn, which was
    // true until speech began auto-submitting. The backend emits `transcript`
    // immediately before running what it heard, so the orb moving here is the
    // first feedback a hands-free operator gets — and they are, by definition,
    // not looking at the prompt box.
    const heard = agentEvent({ type: "transcript", payload: { text: "what is the weather" } });
    expect(heard.orb).toBe("thinking");
    // And it is in the log, so a misheard question can be found afterwards.
    expect(heard.transcript[0].text).toBe("what is the weather");
    expect(heard.transcript[0].type).toBe("transcript");
  });

  it("returns to idle when a heard sentence is refused by single-flight", () => {
    // The one path that emits `transcript` and then no turn: the backend was
    // busy. It says so with a `status`, which is already terminal, so the orb
    // must not be left claiming to think about a request that never ran.
    const heard = agentEvent({ type: "transcript", payload: { text: "read the news" } });
    const refused = reducer(heard, {
      kind: "agent_event",
      event: { type: "status", payload: { text: "I heard you, but I am still working on the last thing." } },
      id: "id-2",
    });
    expect(refused.orb).toBe("idle");
  });

  it("does not clear a pending confirmation when something is heard", () => {
    // Same reasoning as the proactive case: the backend is still blocked on
    // that answer, and dictating a sentence is not answering it.
    const asked = agentEvent({
      type: "confirmation_required",
      payload: { name: "manage_files", input: {} },
    });
    const heard = reducer(asked, {
      kind: "agent_event",
      event: { type: "transcript", payload: { text: "no, leave it alone" } },
      id: "id-5",
    });
    expect(heard.pendingConfirmation).toEqual({ name: "manage_files", input: {} });
  });

  it("does not mutate the previous state object", () => {
    const before = initialState;
    agentEvent({ type: "thought", payload: { text: "x" } });
    expect(before).toEqual({
      orb: "idle",
      connected: false,
      transcript: [],
      screenContext: "",
      pendingConfirmation: null,
    });
  });
});
