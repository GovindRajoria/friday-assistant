import type { OrbState } from "../reducer";

const LABEL: Record<OrbState, string> = {
  idle: "Idle",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Error",
};

export function Orb({ state, connected }: { state: OrbState; connected: boolean }) {
  // A disconnected socket always reads as "Error" regardless of whatever
  // state the reducer last held — the label should never claim the agent
  // is thinking or speaking when nothing is actually listening.
  const label = connected ? LABEL[state] : "Disconnected";
  const visualState = connected ? state : "error";

  return (
    <div className={`orb orb--${visualState}`} role="status" aria-live="polite">
      <span className="orb__ring" />
      <span className="orb__label">{label}</span>
    </div>
  );
}
