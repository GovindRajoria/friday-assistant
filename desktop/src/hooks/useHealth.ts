// Polls GET /health through the preload bridge so the systems panel can show
// what the backend actually loaded, rather than a list hardcoded in the HUD.
//
// The fetch itself happens in the main process (see electron/api.ts for why),
// so everything here is a bridge call, not a network call.
import { useEffect, useState } from "react";

// The backend discovers skills once at import time and never changes the set
// afterwards, so this is a liveness check that happens to carry a roster, not
// a subscription to something that moves. Slow on purpose.
const POLL_INTERVAL_MS = 15_000;

export type HealthStatus =
  // The preload bridge is missing entirely — window.friday is undefined.
  // Distinct from a failed poll: nothing was ever asked.
  | { kind: "unavailable" }
  | { kind: "loading" }
  | { kind: "ok"; skills: string[] }
  | { kind: "unreachable" };

export function useHealth(): HealthStatus {
  const [status, setStatus] = useState<HealthStatus>({ kind: "loading" });

  useEffect(() => {
    // Optional-chained for the same reason every other window.friday call in
    // this project is: a broken preload once threw inside a passive effect
    // and, with no error boundary above it, unmounted the whole HUD. A
    // missing bridge should cost this one panel, not the window.
    const getHealth = window.friday?.getHealth;
    if (!getHealth) {
      setStatus({ kind: "unavailable" });
      return;
    }

    let cancelled = false;

    async function poll() {
      try {
        const report = await window.friday.getHealth();
        if (!cancelled) setStatus({ kind: "ok", skills: report.skills ?? [] });
      } catch {
        // Deliberately not derived from the socket's connected flag: the
        // WebSocket can be open while a health poll times out (a turn
        // saturating the process, a paused machine), and reporting one as
        // the other would show a panel that contradicts the status bar.
        if (!cancelled) setStatus({ kind: "unreachable" });
      }
    }

    poll();
    const timer = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return status;
}
