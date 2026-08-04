# core/nodes/converse.py
"""Reply to conversation without the tool schema in front of the model.

The point is structural. `reason` sends a JSON schema whose `action` is an enum of
45 tool names and requires the field, so every turn is a turn where picking a tool
is the shape of a valid answer. For "hello" that is the whole problem: the model is
being asked which of 45 things to do, when the answer is to say hello back.

This node calls the model with no schema and no tool list at all. There is no
`action` field to fill in, so a tool call is not something it can accidentally
produce — the same reasoning as the confirmation gate defaulting to deny, applied
to routing rather than to safety.

The reply is still the model's own, so it stays in persona and reads like a person
rather than a canned string. A fixed "Hello, Sir." would have solved the bug and
made the assistant worse.
"""
from core import llm_client
from core.config import SETTINGS
from core.state import AgentState

# Long enough to be warm, short enough that it cannot wander into a monologue.
MAX_WORDS = 40


def _system_prompt() -> str:
    assistant_name = SETTINGS["assistant"]["name"]
    address_as = SETTINGS["assistant"]["address_user_as"]
    user_name = SETTINGS["user"].get("name") or "the operator"
    return (
        f"You are {assistant_name}, a local assistant on {user_name}'s machine. "
        f"Address them as '{address_as}'. Your persona is sophisticated, sharp and warm.\n\n"
        "The user has said something conversational — a greeting, thanks, or a "
        "pleasantry. Reply in ONE short sentence, two at most.\n\n"
        "Rules:\n"
        "- Do not list your abilities, your tools, or what you can do. They did not ask.\n"
        "- Do not describe the screen, the time, the weather or anything you would "
        "have to look up. You have looked nothing up.\n"
        "- Do not ask a string of questions. At most, offer to help, once.\n"
        "- Do not invent what they said next or write on their behalf.\n"
        "- No preamble, no quotes around your reply, no stage directions."
    )


def converse_node(state: AgentState) -> dict:
    """Answer conversationally. Never returns an action, so the turn ends here."""
    user_input = state.get("user_input", "")
    try:
        reply = llm_client.chat(
            [{"role": "system", "content": _system_prompt()},
             {"role": "user", "content": user_input}],
            # Warmth is the point here, unlike routing, which wants determinism.
            temperature=0.4,
        )
    except Exception:                                     # noqa: BLE001
        # A greeting must never surface a traceback, and the model being
        # unreachable is exactly when a plain answer matters most.
        return {"final_answer": _fallback(), "steps": state.get("steps", 0)}

    answer = _trim(str(reply or "").strip())
    return {"final_answer": answer or _fallback(), "steps": state.get("steps", 0)}


def _trim(text: str) -> str:
    """Strip the wrappers a local model adds, and cap the length."""
    text = text.strip().strip('"').strip()
    # Some models prefix a role label despite being told not to.
    for prefix in ("FRIDAY:", "Assistant:", "Reply:", "Response:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    words = text.split()
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS]).rstrip(",;:") + "."
    return text


def _fallback() -> str:
    return f"Hello, {SETTINGS['assistant']['address_user_as']}. What can I do for you?"
