# core/nodes/act.py
import json

from core.state import AgentState


def call_key(name: str, params: dict) -> str:
    """A stable identity for one tool call, used to spot exact repeats.

    sort_keys because the model does not emit its parameters in a fixed
    order, and two dicts differing only in key order are the same call.
    """
    try:
        rendered = json.dumps(params, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = repr(params)
    return f"{name}:{rendered}"


def act_node(state: AgentState, active_skills: dict) -> dict:
    name = state.get("action")
    params = state.get("action_input") or {}

    skill = active_skills.get(name)
    if skill is None:
        # The enum makes this unreachable from the model, but a stale
        # transcript replay could still land here.
        return {"observation": f"The tool '{name}' is not loaded.",
                "messages": [*state["messages"],
                             {"role": "user", "content": f"Observation: tool '{name}' is not loaded."}]}

    key = call_key(name, params)
    if key == state.get("last_call"):
        # The model re-proposed the call it just made, byte for byte.
        # Observed live: a single "summarise today's news" request fetched the
        # same feed three times before answering, because nothing told the
        # model it already had the result. Re-running costs another network
        # round trip to produce output identical to what is already in the
        # transcript, so answer the repeat rather than serve it.
        #
        # Scoped to the immediately preceding call, not to every call in the
        # turn: re-reading a file after writing it, or re-scanning after
        # something changed, is legitimate, and only a back-to-back identical
        # repeat is certainly redundant.
        observation = (
            f"You already ran {name} with exactly these parameters and its result is above. "
            "Do not call it again — use that result to answer now."
        )
        return {
            "observation": observation,
            "messages": [*state["messages"], {"role": "user", "content": f"Observation: {observation}"}],
        }

    try:
        result = skill.execute(params)
    except Exception as error:                          # noqa: BLE001 — a failing skill must not kill the loop
        result = {"status": "error", "message": f"Error executing {name}: {error}"}

    observation = result.get("message", str(result))
    return {
        "observation": observation,
        "last_call": key,
        # `data` is optional and additive; skills that omit it are unaffected.
        "detections": (result.get("data") or {}).get("detections", state.get("detections", {})),
        "messages": [*state["messages"], {"role": "user", "content": f"Observation: {observation}"}],
    }
