import type { HealthStatus } from "../hooks/useHealth";

// Every figure here is either observed on this client or came off the wire.
// There is deliberately no latency readout, CPU gauge or token counter — the
// backend publishes none of those, and a HUD that draws numbers it made up
// is worse than one that draws fewer.
export function StatusBar({
  connected,
  health,
  turns,
  events,
}: {
  connected: boolean;
  health: HealthStatus;
  turns: number;
  events: number;
}) {
  const skills = health.kind === "ok" ? String(health.skills.length) : "—";

  return (
    <div className="status">
      <div className={`status__link ${connected ? "" : "status__link--down"}`}>
        <span className="status__dot" aria-hidden="true" />
        <span className="status__link-text">{connected ? "127.0.0.1:8756" : "Link down"}</span>
      </div>
      <dl className="status__stats">
        <div className="status__stat" title="Skills reported by GET /health">
          <dt>Skills</dt>
          <dd>{skills}</dd>
        </div>
        <div className="status__stat" title="Turns completed since this window opened">
          <dt>Turns</dt>
          <dd>{turns}</dd>
        </div>
        <div className="status__stat" title="Events received since this window opened">
          <dt>Events</dt>
          <dd>{events}</dd>
        </div>
      </dl>
    </div>
  );
}
