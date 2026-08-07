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
# Phase 6: the confirmation gate. Emitted by server/app.py's `confirm`
# callable (core/nodes/confirm.py calls it synchronously from the turn's
# worker thread) whenever a destructive skill is chosen. The payload shape
# matches ACTION's ({"name": ..., "input": ...}) rather than a plain
# TextPayload, since the HUD needs the actual proposed call to show a human,
# not just a sentence about it.
CONFIRMATION_REQUIRED = "confirmation_required"
# Proactive: something FRIDAY said without being asked — a due reminder or the
# daily briefing, from core/scheduler.py. Its own type rather than reusing
# ANSWER because it is not the end of a turn: no question preceded it, the orb
# must not move, and a pending confirmation must not be cleared by it.
PROACTIVE = "proactive"
# What the operator was heard to say. Emitted once an utterance recorded in the
# HUD has been transcribed, immediately before the turn it becomes runs — so it
# is the record of what was heard, not a request for permission to act on it.
# It stays a separate type from ANSWER's siblings for two reasons: the HUD
# renders it as the operator's own line rather than the assistant's, and a turn
# rejected by single-flight emits this and then nothing else.
TRANSCRIPT = "transcript"

ALL_TYPES = frozenset({
    THOUGHT, ACTION, OBSERVATION, ANSWER, ANOMALY, ERROR, STATUS, SCREEN_CONTEXT, CONFIRMATION_REQUIRED,
    PROACTIVE, TRANSCRIPT,
})


def envelope(event_type: str, payload: dict) -> dict:
    """Build one event envelope.

    A call site could build `{"type": ..., "payload": ...}` inline, but
    centralising it means a typo in the key name fails once here rather than
    silently at every call site.
    """
    return {"type": event_type, "payload": payload}
