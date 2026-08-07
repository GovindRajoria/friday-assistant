# core/nodes/reason.py
import json

from core import llm_client
from core.prompts import build_system_prompt, build_user_message
from core.registry import NO_ACTION, build_action_schema
from core.shortlist import shortlist
from core.state import AgentState


def reason_node(state: AgentState, active_skills: dict) -> dict:
    # Ten skills in front of the model rather than forty-seven — see
    # core/shortlist.py for the measurement that says the problem is attention
    # over a long list rather than missing information in it.
    #
    # Derived from `user_input` rather than carried in the state, which is what
    # keeps it stable for the whole turn: the input does not change between
    # steps, so every step of a chain computes the same subset. That matters
    # because the schema below is rebuilt on every step, and an enum that gained
    # or lost members mid-chain would let the model name a tool on step two that
    # it could not name on step three.
    offered = shortlist(state["user_input"], active_skills)

    messages = state.get("messages") or [
        {"role": "system", "content": build_system_prompt(offered)},
        {"role": "user", "content": build_user_message(
            state["user_input"], state.get("memory_buffer", ""), state.get("screen_context", ""))},
    ]

    raw = llm_client.chat(messages, fmt=build_action_schema(offered))
    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        # Structured output makes this near-impossible; if it happens, stop
        # cleanly rather than routing on garbage.
        return {"final_answer": "My reasoning came back malformed, so I stopped rather than guess."}

    # The plan is narrated before anything executes — the reason the old text
    # protocol existed, preserved as a schema field. The driver speaks it; this
    # node only records it.
    thought = (decision.get("thought") or "").strip()
    action = (decision.get("action") or NO_ACTION).strip()
    # The schema requires final_answer, so the model writes one even when it is
    # about to call a tool. Anything it says while an action is pending is a
    # guess about a result that has not happened yet — discard it.
    final_answer = "" if action != NO_ACTION else (decision.get("final_answer") or "").strip()

    messages = [*messages, {"role": "assistant", "content": raw}]
    return {
        "messages": messages,
        "thought": thought,
        "narration": [thought] if thought else [],
        "action": action,
        "action_input": decision.get("action_input") or {},
        "final_answer": final_answer,
        "steps": state.get("steps", 0) + 1,
    }
