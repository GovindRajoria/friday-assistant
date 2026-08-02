import { describe, expect, it } from "vitest";
import { initialState, reducer } from "./reducer";
import type { AgentEvent } from "./events";

function agentEvent(event: AgentEvent, id = "id-1") {
  return reducer(initialState, { kind: "agent_event", event, id });
}

describe("reducer", () => {
  it("starts idle and disconnected", () => {
    expect(initialState).toEqual({ orb: "idle", connected: false, transcript: [] });
  });

  it("marks connected on a connected action", () => {
    const state = reducer(initialState, { kind: "connected" });
    expect(state.connected).toBe(true);
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
    ["answer", "speaking"],
    ["status", "speaking"],
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

  it("does not mutate the previous state object", () => {
    const before = initialState;
    agentEvent({ type: "thought", payload: { text: "x" } });
    expect(before).toEqual({ orb: "idle", connected: false, transcript: [] });
  });
});
