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
export function Reactor({ state, connected }: { state: OrbState; connected: boolean }) {
  // Carried over from the component this replaces, and it is behaviour
  // rather than styling: a disconnected socket reads as a fault regardless
  // of whatever the reducer last held. The label must never claim the agent
  // is thinking or speaking when nothing is listening to it.
  const label = connected ? LABEL[state] : "Link down";
  const visualState = connected ? state : "error";

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
