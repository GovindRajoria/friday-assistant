import { type FormEvent, type KeyboardEvent, useRef, useState } from "react";

export function PromptInput({
  onSubmit,
  onCancel,
  disabled,
  busy,
}: {
  onSubmit: (text: string) => void;
  onCancel: () => void;
  disabled: boolean;
  // A turn is in flight. Cancel does nothing between turns — the backend
  // resets the interrupt flag at the start of each one — so the button is
  // only live when there is actually something to stop.
  busy: boolean;
}) {
  const [value, setValue] = useState("");
  // Shell-style recall, kept here rather than in the reducer: it is a
  // property of this input box, not of anything the backend said.
  const historyRef = useRef<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    historyRef.current = [...historyRef.current, value.trim()];
    setCursor(null);
    onSubmit(value);
    setValue("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    const history = historyRef.current;
    if (history.length === 0) return;

    if (event.key === "ArrowUp") {
      event.preventDefault();
      const next = cursor === null ? history.length - 1 : Math.max(0, cursor - 1);
      setCursor(next);
      setValue(history[next]);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      if (cursor === null) return;
      const next = cursor + 1;
      if (next >= history.length) {
        // Past the newest entry is the empty line you were typing on, the
        // same place a shell leaves you.
        setCursor(null);
        setValue("");
      } else {
        setCursor(next);
        setValue(history[next]);
      }
    }
  }

  return (
    <form className="command" onSubmit={handleSubmit}>
      <span className="command__caret" aria-hidden="true">
        &gt;
      </span>
      <input
        className="command__input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={disabled ? "Waiting for the backend…" : "Ask FRIDAY…"}
        disabled={disabled}
        aria-label="Prompt"
      />
      {busy ? (
        <button type="button" className="command__button command__button--stop" onClick={onCancel}>
          Stop
        </button>
      ) : (
        <button type="submit" className="command__button" disabled={disabled || !value.trim()}>
          Send
        </button>
      )}
    </form>
  );
}
