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
    # Latched by the guard once announced, cleared only by "exactly 1 person"
    # plus the laptop. It tracks the anomaly, not the audio: whether anything
    # was muted depends on privacy.auto_mute, which defaults off.
    anomaly_active: bool
    screen_context: str            # ambient VLM description (Phase 4)
    # Identity of the tool call `act` most recently executed, as
    # "name:{sorted json params}". Only used to notice that the model has
    # re-proposed the call it just made, which it does — a single "summarise
    # today's news" fetched the same feed three times before answering.
    last_call: str
    # How many times the repeated-call guard has fired this turn. Past the
    # second, route_after_act stops the chain rather than letting the model pick
    # yet another tool — telling it to answer does not reliably make it answer.
    repeated_calls: int
    # Tool calls actually executed this turn, and how many of those in a row went
    # to the same tool. Both bound a wandering turn more tightly than `steps`,
    # which counts reasoning passes and so allows a dozen tool calls before it
    # trips. See core/nodes/act.py for the live transcript that motivated them.
    tool_calls: int
    same_tool_streak: int
    last_tool: str
    action_approved: bool          # set by core/nodes/confirm.py; read by route_after_confirm (Phase 6)
