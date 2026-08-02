import type { TranscriptEntry } from "../reducer";

export function Transcript({ entries }: { entries: TranscriptEntry[] }) {
  return (
    <div className="transcript">
      {entries.map((entry) => (
        <div key={entry.id} className={`transcript__entry transcript__entry--${entry.type}`}>
          <span className="transcript__type">{entry.type}</span>
          <span className="transcript__text">{entry.text}</span>
        </div>
      ))}
    </div>
  );
}
