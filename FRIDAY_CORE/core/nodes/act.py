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
        # Counted, because telling the model to answer now does not reliably make
        # it answer now. Observed live on 2026-08-04 with 45 skills loaded: the
        # repeat guard fired, and instead of answering the model picked a
        # different unrelated tool, twice, and kept going for twenty steps. With
        # more tools available there are simply more ways to avoid concluding.
        # The count is what `route_after_act` escalates on.
        repeats = state.get("repeated_calls", 0) + 1
        observation = (
            f"You already ran {name} with exactly these parameters and its result is above. "
            "Do not call it again — use that result to answer now."
        )
        if repeats > 1:
            observation += (
                " This is the second time. Do not choose another tool either: answer "
                "from what is already in this transcript, or say plainly that you could "
                "not find out."
            )
        return {
            "observation": observation,
            "repeated_calls": repeats,
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
        # Counted so the graph can bound a turn by tool calls, which is a tighter
        # and more meaningful limit than `steps`. Observed live on 2026-08-04:
        # "what is 15 percent of 240" — arithmetic the prompt explicitly forbids
        # using a tool for — produced web_search, describe_screen, run_command and
        # then nine manage_settings calls with varying parameters, so the
        # identical-repeat guard never fired, and the turn died on the step bound
        # 37 seconds later with no answer at all.
        "tool_calls": state.get("tool_calls", 0) + 1,
        # Consecutive calls to the same tool regardless of parameters. Nine in a
        # row is never a plan; it is a model hunting for a result that is not
        # there.
        "same_tool_streak": (state.get("same_tool_streak", 0) + 1
                             if state.get("last_tool") == name else 1),
        "last_tool": name,
        # `data` is optional and additive; skills that omit it are unaffected.
        "detections": (result.get("data") or {}).get("detections", state.get("detections", {})),
        "messages": [*state["messages"], {"role": "user", "content": f"Observation: {observation}"}],
    }
