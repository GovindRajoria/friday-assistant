import { useCallback, useEffect, useState } from "react";

// Edits config/about_me.md — the operator's own biography, which the backend
// folds into every system prompt.
//
// A full overlay rather than a rail panel: this is prose someone sits and
// writes, and a 288px column is not somewhere anybody writes prose. It is
// mounted only while open, so the file is read fresh each time and two
// sessions cannot show stale text at each other.
//
// The backend re-reads the file on every turn, so a save takes effect on the
// next question with no restart. Worth saying in the UI, because "do I need
// to restart it?" is the obvious question and the answer is no.
export function ProfileEditor({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const read = window.friday?.readProfile;
    if (!read) {
      setError("The bridge to the app is unavailable, so the profile cannot be loaded.");
      setText("");
      return;
    }
    window.friday
      .readProfile()
      .then((value) => {
        if (!cancelled) setText(value);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Could not read the profile file.");
          setText("");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(async () => {
    if (text === null) return;
    setSaving(true);
    setError(null);
    try {
      const ok = await window.friday?.writeProfile(text);
      if (ok) onClose();
      else setError("The profile could not be written to disk.");
    } catch {
      setError("The profile could not be written to disk.");
    } finally {
      setSaving(false);
    }
  }, [text, onClose]);

  return (
    <div className="overlay" role="dialog" aria-label="About me">
      <div className="overlay__panel">
        <header className="overlay__header">
          <h2 className="overlay__title">About me</h2>
          <p className="overlay__subtitle">
            Written into every system prompt, so answers are grounded in who you are. Saved to
            <code> config/about_me.md</code>, which is git-ignored and never leaves this machine. Takes effect on your
            next question — no restart.
          </p>
        </header>

        {text === null ? (
          <p className="panel__note">Loading…</p>
        ) : (
          <textarea
            className="overlay__textarea"
            value={text}
            onChange={(event) => setText(event.target.value)}
            spellCheck={false}
            aria-label="Your biography, in markdown"
          />
        )}

        {error && <p className="panel__note panel__note--warn">{error}</p>}

        <div className="overlay__buttons">
          <button type="button" className="overlay__button" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="overlay__button overlay__button--primary"
            onClick={save}
            disabled={saving || text === null}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
