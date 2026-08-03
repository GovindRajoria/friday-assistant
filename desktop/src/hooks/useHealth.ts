// Polls GET /health through the preload bridge so the systems panel can show
// what the backend actually loaded, rather than a list hardcoded in the HUD.
//
// The fetch itself happens in the main process (see electron/api.ts for why),
// so everything here is a bridge call, not a network call.
import { useEffect, useState } from "react";
import type { FridayApi } from "../../electron/api";

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
  // `detail` is why, in one sentence, from what main saw at launch. An
  // unreachable backend with no explanation is the single most confusing
  // state this app can be in — someone installed it, got "Disconnected",
  // and had nothing to go on.
  | { kind: "unreachable"; detail: string };

function explain(report: Awaited<ReturnType<FridayApi["getBackendStatus"]>> | undefined): string {
  switch (report?.kind) {
    case "missing":
      return `No backend found. The installer ships the shell only — point FRIDAY_CORE_DIR at your FRIDAY_CORE folder, or put one beside the app. Looked for ${report.pythonExe}`;
    case "silent":
      return `A backend was started from ${report.coreDir} but never answered within ${Math.round(report.timeoutMs / 1000)}s.`;
    case "attached":
    case "spawned":
      return "The backend answered at launch but is not responding now. It may have stopped.";
    default:
      return "The backend is not answering on 127.0.0.1:8756.";
  }
}

export function useHealth(): { health: HealthStatus; refresh: () => void } {
  const [status, setStatus] = useState<HealthStatus>({ kind: "loading" });
  // Bumped to re-run the effect on demand — after locating a backend, waiting
  // out the fifteen-second poll would look like the fix had not worked.
  const [nonce, setNonce] = useState(0);

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
        //
        // The launch report is fetched only on failure. It never changes
        // after startup, so polling it alongside a healthy backend would be
        // asking main the same question every fifteen seconds forever.
        let report;
        try {
          report = await window.friday?.getBackendStatus();
        } catch {
          report = undefined;
        }
        if (!cancelled) setStatus({ kind: "unreachable", detail: explain(report) });
      }
    }

    poll();
    const timer = window.setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [nonce]);

  return { health: status, refresh: () => setNonce((value) => value + 1) };
}
