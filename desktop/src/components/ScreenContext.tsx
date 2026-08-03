// The watcher's latest ambient screen description (Phase 4).
//
// This used to render nothing when there was no description, which was right
// for the one-line status strip it was. It is a whole panel now — one the
// operator deliberately switched to — and an empty panel reads as broken
// rather than as "screen awareness is switched off", so the empty case
// explains itself instead.
export function ScreenContext({ text }: { text: string }) {
  if (!text) {
    return (
      <p className="panel__note">
        No screen description yet. Continuous screen awareness stays off unless <code>screen.enabled</code> is set in
        settings, and the first describe pass only runs once the desktop changes.
      </p>
    );
  }

  return (
    <div className="vision">
      <div className="vision__frame">
        <p className="vision__text">{text}</p>
      </div>
      <p className="vision__caveat">
        Written by a small local vision model. Ambient context, often wrong in detail, and validated by nothing
        downstream.
      </p>
    </div>
  );
}
