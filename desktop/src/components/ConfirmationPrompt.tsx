import type { ActionPayload } from "../events";

// Renders nothing when nothing is pending — the common case. Shown only
// while a destructive skill (manifest "destructive": true) is blocked on
// core/nodes/confirm.py waiting for this exact human answer; approving or
// denying sends {"type": "confirm", "approved": ...} back over the socket.
//
// It renders over the panel rather than under it, and the parameters are
// listed one per line rather than as a JSON blob. Both are on purpose: this
// is the one moment the HUD asks a human to authorise something irreversible,
// and a path buried in a single-line object is a path nobody reads before
// clicking. The backend denies on its own after sixty seconds of silence.
export function ConfirmationPrompt({
  pending,
  onApprove,
  onDeny,
}: {
  pending: ActionPayload | null;
  onApprove: () => void;
  onDeny: () => void;
}) {
  if (!pending) return null;

  const parameters = Object.entries(pending.input);

  return (
    <div className="confirm" role="alertdialog" aria-label="Confirmation required">
      <div className="confirm__header">
        <span className="confirm__badge">Authorisation required</span>
      </div>
      <p className="confirm__call">{pending.name}</p>
      {parameters.length > 0 && (
        <dl className="confirm__params">
          {parameters.map(([key, value]) => (
            <div key={key} className="confirm__param">
              <dt>{key}</dt>
              <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="confirm__buttons">
        <button type="button" className="confirm__deny" onClick={onDeny}>
          Deny
        </button>
        <button type="button" className="confirm__approve" onClick={onApprove}>
          Approve
        </button>
      </div>
    </div>
  );
}
