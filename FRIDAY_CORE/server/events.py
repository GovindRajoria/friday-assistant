# server/events.py
"""Typed event envelopes streamed to the HUD over the WebSocket.

Kept small and JSON-serialisable on purpose — `{"type": ..., "payload": {...}}`
— so the client only has to switch on `type`; it never needs to know
anything about the shape of LangGraph's state.

`SCREEN_CONTEXT` is Phase 4 (ambient VLM description of the desktop). The
constant is defined now so the event vocabulary is stable across phases;
nothing in Phase 2 emits it.
"""

THOUGHT = "thought"
ACTION = "action"
OBSERVATION = "observation"
ANSWER = "answer"
ANOMALY = "anomaly"
ERROR = "error"
STATUS = "status"
SCREEN_CONTEXT = "screen_context"

ALL_TYPES = frozenset({THOUGHT, ACTION, OBSERVATION, ANSWER, ANOMALY, ERROR, STATUS, SCREEN_CONTEXT})


def envelope(event_type: str, payload: dict) -> dict:
    """Build one event envelope.

    A call site could build `{"type": ..., "payload": ...}` inline, but
    centralising it means a typo in the key name fails once here rather than
    silently at every call site.
    """
    return {"type": event_type, "payload": payload}
