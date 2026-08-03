import { type FormEvent, type KeyboardEvent, type ReactNode, useEffect, useRef, useState } from "react";

export function PromptInput({
  onSubmit,
  onCancel,
  disabled,
  busy,
  dictation,
  children,
}: {
  onSubmit: (text: string) => void;
  onCancel: () => void;
  disabled: boolean;
  // A turn is in flight. Cancel does nothing between turns — the backend
  // resets the interrupt flag at the start of each one — so the button is
  // only live when there is actually something to stop.
  busy: boolean;
  // What the microphone was heard to say, or null. It is put in the box and
  // left there: the operator reads it and presses send. Auto-submitting would
  // be one keystroke shorter and would let a misheard sentence become a
  // request this assistant carries out, and the things it can carry out
  // include deleting files.
  dictation: { id: string; text: string } | null;
  // The microphone button, passed in rather than imported here so this
  // component keeps knowing nothing about audio.
  children?: ReactNode;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  // Shell-style recall, kept here rather than in the reducer: it is a
  // property of this input box, not of anything the backend said.
  const historyRef = useRef<string[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);

  // Keyed on the dictation's id, not its text: saying the same sentence twice
  // has to land twice, and depending on the value changing would swallow the
  // second one.
  useEffect(() => {
    if (!dictation) return;
    setValue(dictation.text);
    setCursor(null);
    // Focused with the caret at the end, so a wrong word can be corrected and
    // sent without reaching for the mouse. This is the entire point of not
    // submitting it automatically.
    const input = inputRef.current;
    input?.focus();
    input?.setSelectionRange(dictation.text.length, dictation.text.length);
  }, [dictation?.id]);

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
        ref={inputRef}
        className="command__input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={disabled ? "Waiting for the backend…" : "Ask FRIDAY, or press Speak…"}
        disabled={disabled}
        aria-label="Prompt"
      />
      {children}
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
