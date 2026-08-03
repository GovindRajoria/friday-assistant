import type { ActionPayload } from "../events";

// Renders nothing when nothing is pending — the common case. Shown only
// while a destructive skill (manifest "destructive": true) is blocked on
// core/nodes/confirm.py waiting for this exact human answer; approving or
// denying sends {"type": "confirm", "approved": ...} back over the socket.
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

  return (
    <div className="confirmation-prompt">
      <span className="confirmation-prompt__text">
        Approve <strong>{pending.name}</strong>({JSON.stringify(pending.input)})?
      </span>
      <div className="confirmation-prompt__buttons">
        <button type="button" className="confirmation-prompt__approve" onClick={onApprove}>
          Approve
        </button>
        <button type="button" className="confirmation-prompt__deny" onClick={onDeny}>
          Deny
        </button>
      </div>
    </div>
  );
}
