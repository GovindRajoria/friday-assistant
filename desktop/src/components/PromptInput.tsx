import { type FormEvent, useState } from "react";

export function PromptInput({
  onSubmit,
  onCancel,
  disabled,
}: {
  onSubmit: (text: string) => void;
  onCancel: () => void;
  disabled: boolean;
}) {
  const [value, setValue] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    onSubmit(value);
    setValue("");
  }

  return (
    <form className="prompt-input" onSubmit={handleSubmit}>
      <input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Ask FRIDAY..."
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !value.trim()}>
        Send
      </button>
      <button type="button" onClick={onCancel} disabled={disabled}>
        Cancel
      </button>
    </form>
  );
}
