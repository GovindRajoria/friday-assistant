# core/nodes/confirm.py
"""The confirmation gate: a human sign-off before a destructive skill runs.

core/graph.py routes here instead of straight to `act` whenever the chosen
skill's manifest carries `"destructive": True`. The `confirm` callable is
injected into build_graph exactly like `emit` — core/main.py wires a stdin
prompt, server/app.py wires a WebSocket round trip that blocks on a
threading.Event — so this node itself knows nothing about consoles or
sockets, only whether the answer was yes.

Default-deny is the point of this file. A caller that never supplies a
`confirm` callable gets a hard "no" on every destructive action rather than
an open door. That is deliberate: the confirmation gate is worthless if the
one thing every new integration point has to remember is to wire it up
correctly, so the unwired case fails safe instead of failing open.
"""
from core.state import AgentState


def confirm_node(state: AgentState, confirm) -> dict:
    action = state.get("action")
    action_input = state.get("action_input") or {}
    thought = state.get("thought", "")

    approved = False
    if confirm is not None:
        try:
            approved = bool(confirm(action, action_input, thought))
        except Exception:  # noqa: BLE001 — a raising confirm gate must deny, not crash the turn
            approved = False

    if approved:
        return {"action_approved": True}

    # The denial becomes the next Observation rather than ending the turn
    # silently. The model can acknowledge it ("understood, I won't") and
    # keep going; a model that just keeps re-proposing the same denied
    # action is eventually stopped by the ordinary step bound in
    # route_after_reason, the same as any other loop that never converges.
    observation = f"Confirmation denied for '{action}'. The action was not performed."
    return {
        "action_approved": False,
        "observation": observation,
        "messages": [*state["messages"], {"role": "user", "content": f"Observation: {observation}"}],
    }
