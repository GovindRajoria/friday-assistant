# core/nodes/act.py
from core.state import AgentState


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

    try:
        result = skill.execute(params)
    except Exception as error:                          # noqa: BLE001 — a failing skill must not kill the loop
        result = {"status": "error", "message": f"Error executing {name}: {error}"}

    observation = result.get("message", str(result))
    return {
        "observation": observation,
        # `data` is optional and additive; skills that omit it are unaffected.
        "detections": (result.get("data") or {}).get("detections", state.get("detections", {})),
        "messages": [*state["messages"], {"role": "user", "content": f"Observation: {observation}"}],
    }
