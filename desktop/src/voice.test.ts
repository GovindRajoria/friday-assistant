// One control now stands for two capabilities, so the precedence between their
// states is a decision rather than an accident — and this is where it is pinned.
//
// The case that matters most is the last one: an open microphone must never be
// labelled as anything an operator could read as "closed". That is the whole
// argument for the mode being opt-in, and it would be undone by a label bug
// rather than by a change of policy.
import { describe, expect, it } from "vitest";
import { microphoneIsOpen, voiceStatus } from "./voice";
import type { ListeningState } from "./hooks/useAlwaysListening";
import type { MicState } from "./hooks/useMicrophone";

const off: ListeningState = { kind: "off" };
const idle: MicState = { kind: "idle" };

describe("voiceStatus", () => {
  it("is off when nothing is open and nothing is running", () => {
    expect(voiceStatus(off, idle, false)).toBe("off");
  });

  it.each([
    ["waiting", "waiting"],
    ["hearing", "hearing"],
    ["starting", "starting"],
  ] as const)("reports the hands-free %s state", (kind, expected) => {
    expect(voiceStatus({ kind }, idle, false)).toBe(expected);
  });

  it("shows a hotkey recording ahead of every hands-free state", () => {
    // A press is something the operator did a second ago; they need to see it
    // took. `opening` counts, because that is the window in which speech is not
    // being captured yet and saying "recording" during it costs them a word.
    expect(voiceStatus({ kind: "waiting" }, { kind: "recording" }, false)).toBe("recording");
    expect(voiceStatus({ kind: "waiting" }, { kind: "opening" }, false)).toBe("recording");
  });

  it("shows a running turn ahead of hearing, so it never looks like it heard itself", () => {
    // While the answer plays, the open microphone genuinely is picking up sound
    // — its own voice, from a speaker, because SAPI plays out of process where
    // the stream's echo cancellation cannot reach it. Labelling that "Hearing
    // you…" reads as though it had mistaken itself for the operator.
    expect(voiceStatus({ kind: "hearing" }, idle, true)).toBe("working");
    expect(voiceStatus({ kind: "waiting" }, idle, true)).toBe("working");
    expect(voiceStatus(off, idle, true)).toBe("working");
  });

  it.each(["denied", "unsupported"] as const)(
    "reports an unusable microphone from either mode (%s)", (kind) => {
      expect(voiceStatus({ kind, detail: "x" } as ListeningState, idle, false)).toBe("unavailable");
      expect(voiceStatus(off, { kind, detail: "x" } as MicState, false)).toBe("unavailable");
    });

  it("reports a broken microphone even while a turn is running", () => {
    // Otherwise a denied permission hides behind "Working…" for the length of
    // every turn, and the operator keeps talking into a device that is refused.
    expect(voiceStatus({ kind: "denied" }, idle, true)).toBe("unavailable");
  });
});

describe("microphoneIsOpen", () => {
  it("is true whenever either mode holds the device", () => {
    expect(microphoneIsOpen({ kind: "waiting" }, idle)).toBe(true);
    expect(microphoneIsOpen({ kind: "hearing" }, idle)).toBe(true);
    expect(microphoneIsOpen(off, { kind: "recording" })).toBe(true);
    expect(microphoneIsOpen(off, { kind: "opening" })).toBe(true);
  });

  it("is true during a turn, when the label says Working", () => {
    // The label and the light answer different questions. "Working…" is about
    // the turn; the light is about the device, and an open microphone that
    // stops showing as open for the length of every answer is exactly the
    // quiet reversal this mode being opt-in exists to prevent.
    expect(microphoneIsOpen({ kind: "waiting" }, idle)).toBe(true);
    expect(voiceStatus({ kind: "waiting" }, idle, true)).toBe("working");
  });

  it("is false when nothing is open, including while starting", () => {
    // `starting` is before the permission prompt resolves — the device is not
    // held yet, and claiming it is would be the lie in the other direction.
    expect(microphoneIsOpen(off, idle)).toBe(false);
    expect(microphoneIsOpen({ kind: "starting" }, idle)).toBe(false);
    expect(microphoneIsOpen({ kind: "denied" }, { kind: "denied" })).toBe(false);
  });
});
