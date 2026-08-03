// The watcher's latest ambient screen description (Phase 4).
//
// The empty state is one line, not a paragraph. This sits permanently in a
// rail rather than behind a tab the operator switched to, and screen
// awareness is off by default — so the "nothing here" case is what most
// installs show all the time and it has to stay out of the way.
export function ScreenContext({ text }: { text: string }) {
  if (!text) {
    return (
      <p className="panel__note">
        Off — set <code>screen.enabled</code> in settings.
      </p>
    );
  }

  return (
    <div className="vision">
      <div className="vision__frame">
        <p className="vision__text">{text}</p>
      </div>
      <p className="vision__caveat">Small local vision model. Ambient context, often wrong in detail.</p>
    </div>
  );
}
