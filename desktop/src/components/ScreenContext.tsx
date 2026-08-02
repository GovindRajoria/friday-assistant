// A small ambient status line for the watcher's latest screen description
// (Phase 4). Renders nothing when there is no description yet — the common
// case for anyone running with screen.enabled: false, or before the first
// describe pass completes after startup.
export function ScreenContext({ text }: { text: string }) {
  if (!text) return null;

  return (
    <div className="screen-context" title={text}>
      <span className="screen-context__label">On screen</span>
      <span className="screen-context__text">{text}</span>
    </div>
  );
}
