# core/session.py
"""The turn runner shared by the console driver and the WebSocket server.

This used to live inline in core/main.py:_run_graph. It moved here once a
second consumer (server/app.py) needed to drive the same graph without
duplicating two pieces of hard-won logic:

  1. The `if not delta: continue` guard. A node that returns `{}` (the common
     "nothing changed" path through anomaly_guard) streams as `None` under
     `stream_mode="updates"` in langgraph 1.2.10, not `{}` — observed
     directly against this project, not documented behavior. Skipping it is
     required, not defensive: without this check, a routine turn with no
     anomaly crashes the driver.
  2. The interrupt early-return, which deliberately leaves the flag set
     rather than resetting it here. The console loop resets it at the top of
     its own while-loop; batch testing relies on it staying set so the outer
     test-case loop sees it and stops the whole run, not just one case.

`emit(event_type, payload)` is the single sink. Every consumer of a turn gets
its events through this one callback; nothing here calls a speaker or a
socket directly, which is what keeps narration from being spoken or streamed
twice once there are two consumers reading the same run.
"""
from core.registry import NO_ACTION

# Narration text spoken by the console driver. Kept distinct from
# ACTION/OBSERVATION, which were never spoken in Phase 1 and stay silent by
# default in the console wrapper — only the WebSocket layer surfaces them.
SPOKEN_EVENT_TYPES = ("thought", "anomaly", "status", "answer")


def run_turn(graph, user_input, memory_buffer, interrupter, emit, history_length=10) -> str:
    """Drive `graph` to completion for one user turn, streaming events through `emit`.

    Returns the final answer text. `memory_buffer` is the caller's running
    list of prior turns (`"User: ..."` / `"FRIDAY: ..."` strings); only the
    trailing `history_length` entries are folded into the prompt, mirroring
    the trim policy the caller already applies to the list itself.
    """
    state = {
        "user_input": user_input,
        "memory_buffer": "\n".join(memory_buffer[-history_length:]),
        "messages": [],
        "steps": 0,
    }
    final_answer = ""

    for update in graph.stream(state, {"recursion_limit": 40}, stream_mode="updates"):
        if interrupter.interrupted:
            # Leave the flag set rather than resetting it here. The voice
            # loop resets it at the top of its own while-loop; batch
            # testing relies on it staying set so the outer test-case
            # loop sees it and stops the whole run, not just this case.
            message = "Manual override. Thought process terminated."
            # Emitted as `answer` rather than `status` because it is the last
            # event of the turn. A socket client has no other way to learn a
            # turn is over, so a non-terminal type here left the HUD waiting
            # on an answer that was never coming — found by cancelling a real
            # turn, not by any unit test, because the console driver reads the
            # return value below and never noticed.
            emit("answer", {"text": message})
            return message

        for node_name, delta in update.items():
            # A node that returns {} (anomaly_guard on the common "nothing
            # changed" path) streams as None under stream_mode="updates",
            # not {} — observed directly against langgraph 1.2.10, not
            # documented behavior. Skipping it here is required, not
            # defensive: without this check, a routine turn with no
            # anomaly crashes the driver.
            if not delta:
                continue

            # Node-specific event typing. `narration` alone told the console
            # driver everything it needed (one thing to speak); the socket
            # needs to know which node produced a line so the HUD can show
            # tool calls and observations distinctly from spoken narration.
            if node_name == "act":
                emit("observation", {"text": delta.get("observation", "")})
            elif node_name == "anomaly_guard":
                for line in delta.get("narration", []):
                    emit("anomaly", {"text": line})
            else:
                for line in delta.get("narration", []):
                    emit("thought", {"text": line})
                action = delta.get("action")
                if action and action != NO_ACTION:
                    emit("action", {"name": action, "input": delta.get("action_input", {})})

            if delta.get("final_answer"):
                final_answer = delta["final_answer"]

    if not final_answer:
        final_answer = "I worked through that but have no answer to give."
    emit("answer", {"text": final_answer})
    return final_answer
