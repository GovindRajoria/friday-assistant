import { useEffect, useRef, useState } from "react";
import type { AgentEventType } from "../events";
import type { TranscriptEntry } from "../reducer";

// Only the chatty step-by-step types can be muted. `answer`, `error` and
// `anomaly` are deliberately absent from this list and can never be hidden:
// a filter that swallowed the end of a turn would leave the HUD looking
// frozen mid-reasoning, which is worse than a busy log.
const FILTERABLE: AgentEventType[] = ["thought", "action", "observation", "status"];

const SHORT_LABEL: Partial<Record<AgentEventType, string>> = {
  thought: "THT",
  action: "ACT",
  observation: "OBS",
  answer: "ANS",
  anomaly: "ALM",
  error: "ERR",
  status: "SYS",
  screen_context: "VIS",
  confirmation_required: "ASK",
};

export function Transcript({ entries }: { entries: TranscriptEntry[] }) {
  // Component-local rather than reducer state on purpose: the reducer is the
  // pure projection of what arrived over the socket, and a panel's view
  // preferences are not something the backend said.
  const [muted, setMuted] = useState<Set<AgentEventType>>(() => new Set());
  const endRef = useRef<HTMLDivElement | null>(null);

  const visible = entries.filter((entry) => !muted.has(entry.type));
  const hidden = entries.length - visible.length;

  // Follow the tail as a turn streams in.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [visible.length]);

  function toggle(type: AgentEventType) {
    setMuted((previous) => {
      const next = new Set(previous);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  return (
    <div className="transcript">
      <div className="transcript__filters">
        {FILTERABLE.map((type) => (
          <button
            key={type}
            type="button"
            className={`chip ${muted.has(type) ? "chip--off" : ""}`}
            onClick={() => toggle(type)}
            aria-pressed={!muted.has(type)}
            title={`Show or hide ${type} events`}
          >
            {SHORT_LABEL[type]}
          </button>
        ))}
        {hidden > 0 && <span className="transcript__hidden">{hidden} hidden</span>}
      </div>

      <div className="transcript__scroll">
        {entries.length === 0 && <p className="panel__note">No activity yet. Ask something below.</p>}
        {visible.map((entry) => (
          <div key={entry.id} className={`transcript__entry transcript__entry--${entry.type}`}>
            <span className="transcript__type">{SHORT_LABEL[entry.type] ?? entry.type}</span>
            <span className="transcript__text">{entry.text}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
