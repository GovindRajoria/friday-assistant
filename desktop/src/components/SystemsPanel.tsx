import type { HealthStatus } from "../hooks/useHealth";

// The skill roster the backend actually loaded, read from GET /health.
//
// Everything on this panel comes off the wire. The endpoint returns names
// and nothing else, so names and a count are all that is shown — no latency
// figures, no memory gauges, nothing this HUD would have to invent.
export function SystemsPanel({ health }: { health: HealthStatus }) {
  if (health.kind === "loading") {
    return <p className="panel__note">Querying backend…</p>;
  }

  if (health.kind === "unavailable") {
    // The preload bridge is gone, which is a different failure from the
    // backend being down — nothing was ever asked, so saying "unreachable"
    // here would point at the wrong thing.
    return <p className="panel__note panel__note--warn">Bridge unavailable — the HUD cannot query the backend.</p>;
  }

  if (health.kind === "unreachable") {
    return <p className="panel__note panel__note--warn">Backend did not answer the last health probe.</p>;
  }

  if (health.skills.length === 0) {
    return <p className="panel__note panel__note--warn">Backend is up but reports no loaded skills.</p>;
  }

  return (
    <div className="systems">
      <div className="systems__count">
        <span className="systems__count-value">{health.skills.length}</span>
        <span className="systems__count-label">skills online</span>
      </div>
      <ul className="systems__list">
        {health.skills.map((skill) => (
          <li key={skill} className="systems__item">
            <span className="systems__dot" aria-hidden="true" />
            <span className="systems__name">{skill}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
