import type { OrbState } from "../reducer";

const LABEL: Record<OrbState, string> = {
  idle: "Standing by",
  thinking: "Reasoning",
  speaking: "Speaking",
  error: "Fault",
};

// Four concentric rings drawn as SVG rather than nested divs, so the dashed
// arcs can be described as stroke patterns and spun with one CSS rotation
// each instead of a stack of masked circles. No animation library — every
// motion here is a keyframe in App.css.
export function Reactor({
  state,
  connected,
  listening,
}: {
  state: OrbState;
  connected: boolean;
  // The microphone is open in this window. Not an OrbState: nothing on the
  // socket reports it, and the reducer is a projection of what arrived over
  // the socket. It also has to be able to show while a turn is still
  // reasoning, since dictating the next request during an answer is allowed.
  listening: boolean;
}) {
  // Carried over from the component this replaces, and it is behaviour
  // rather than styling: a disconnected socket reads as a fault regardless
  // of whatever the reducer last held. The label must never claim the agent
  // is thinking or speaking when nothing is listening to it.
  //
  // Recording outranks both, because it is the one state where something is
  // being captured off the operator's microphone. That should never be
  // obscured by a label about what the model is doing.
  const label = !connected ? "Link down" : listening ? "Listening" : LABEL[state];
  const visualState = !connected ? "error" : listening ? "listening" : state;

  return (
    <div className={`reactor reactor--${visualState}`} role="status" aria-live="polite">
      <svg className="reactor__core" viewBox="0 0 120 120" aria-hidden="true">
        {/* Outermost: ticked bezel, counter-rotating against the ring below
            it so the assembly reads as machinery rather than one spinner. */}
        <circle className="reactor__bezel" cx="60" cy="60" r="56" />
        <circle className="reactor__ring reactor__ring--outer" cx="60" cy="60" r="48" />
        <circle className="reactor__ring reactor__ring--middle" cx="60" cy="60" r="37" />
        <circle className="reactor__ring reactor__ring--inner" cx="60" cy="60" r="27" />
        <circle className="reactor__center" cx="60" cy="60" r="16" />
        <circle className="reactor__pulse" cx="60" cy="60" r="16" />
      </svg>
      <span className="reactor__label">{label}</span>
    </div>
  );
}
