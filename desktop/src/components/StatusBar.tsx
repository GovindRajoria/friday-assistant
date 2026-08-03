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
    <div className="status-bar">
      <span className={`status-bar__link ${connected ? "" : "status-bar__link--down"}`}>
        <span className="status-bar__dot" aria-hidden="true" />
        {connected ? "LINK 127.0.0.1:8756" : "LINK DOWN"}
      </span>
      <span className="status-bar__stat" title="Skills reported by GET /health">
        SKL {skills}
      </span>
      <span className="status-bar__stat" title="Turns completed since this window opened">
        TRN {turns}
      </span>
      <span className="status-bar__stat" title="Events received since this window opened">
        EVT {events}
      </span>
    </div>
  );
}
