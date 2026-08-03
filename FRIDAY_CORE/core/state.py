# core/state.py
"""The single source of loop truth for the reasoning graph.

Every node reads and writes this shape. Keeping it a plain TypedDict (rather
than a class with methods) is what lets LangGraph merge partial node returns
into the running state without any custom logic.
"""
from operator import add
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    user_input: str
    memory_buffer: str             # prior turns, so a follow-up question has a referent
    messages: list[dict]           # the running transcript sent to the model
    thought: str
    action: str | None
    action_input: dict
    observation: str
    final_answer: str | None
    steps: int                     # the bound that brain.py never had on chaining
    narration: Annotated[list[str], add]   # everything to speak / stream to the HUD
    detections: dict[str, int]     # structured output of the last scan
    anomaly_active: bool           # latched by the guard, cleared only by "exactly 1 person"
    screen_context: str            # ambient VLM description (Phase 4)
    # Identity of the tool call `act` most recently executed, as
    # "name:{sorted json params}". Only used to notice that the model has
    # re-proposed the call it just made, which it does — a single "summarise
    # today's news" fetched the same feed three times before answering.
    last_call: str
    action_approved: bool          # set by core/nodes/confirm.py; read by route_after_confirm (Phase 6)
