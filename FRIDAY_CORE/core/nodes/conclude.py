# core/nodes/conclude.py
"""Make the model answer, with the tools taken away.

`abort` already existed for a turn that ran past its step bound, and what it says
is "I worked through several steps without reaching a conclusion, so I stopped."
That is honest and it is a waste: by then the transcript usually contains enough to
answer, and the operator gets an apology instead.

This node is reached when a turn has spent its tool budget without producing an
answer. It hands the model the transcript with **no schema and no tool list**, so
the only thing it can produce is prose. Same structural argument as
`core/nodes/converse.py`: when the goal is an answer rather than a decision, do not
put 45 possible decisions in front of it.

Observed live on 2026-08-04, which is why this exists: "what is 15 percent of 240" —
arithmetic the system prompt explicitly forbids using a tool for — produced
web_search, describe_screen, run_command and nine consecutive manage_settings calls
with varying parameters, then died on the step bound 37 seconds later with no answer.
The model was never short of the ability to answer; it was short of a turn in which
answering was the only option.
"""
from core import llm_client
from core.config import SETTINGS
from core.state import AgentState

SYSTEM = (
    "You are {name}, a local assistant. Address the user as '{address_as}'.\n\n"
    "You have finished using tools for this turn — there are none left available to "
    "you. Answer the user's question now, in one or two sentences.\n\n"
    "Rules:\n"
    "- If the tool results above contain the answer, use them and nothing else.\n"
    "- If the question is arithmetic or general knowledge, just answer it directly. "
    "You can do this without any tool.\n"
    "- If you genuinely do not know and the results did not help, say so plainly in "
    "one sentence. Do not guess at specifics, and do not list your abilities.\n"
    "- No preamble, no quotes, no stage directions."
)


def conclude_node(state: AgentState) -> dict:
    name = SETTINGS["assistant"]["name"]
    address_as = SETTINGS["assistant"]["address_user_as"]

    # The transcript so far, trimmed to the observations that might carry the
    # answer. The full message list includes every re-prompt and would spend the
    # context window restating instructions the model no longer needs.
    history = [message for message in (state.get("messages") or [])
               if str(message.get("content", "")).startswith("Observation:")][-6:]
    transcript = "\n".join(str(message["content"]) for message in history)

    user_block = f"The user asked: {state.get('user_input', '')}"
    if transcript:
        user_block += f"\n\nWhat the tools returned:\n{transcript}"

    try:
        reply = llm_client.chat(
            [{"role": "system", "content": SYSTEM.format(name=name, address_as=address_as)},
             {"role": "user", "content": user_block}],
            temperature=0.1,
        )
    except Exception:                                     # noqa: BLE001
        return {"final_answer": _fallback()}

    answer = str(reply or "").strip().strip('"').strip()
    return {"final_answer": answer or _fallback()}


def _fallback() -> str:
    return ("I used several tools on that and did not get to an answer, so I would "
            "rather say so than guess.")
