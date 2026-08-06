# core/graph.py
"""The reasoning loop as an explicit state machine.

This replaces the analyze_intent / resume_react ping-pong in core/brain.py.
Three things change and each closes a specific defect:

  1. Routing reads a JSON field instead of string-matching an `Action:` line,
     so a reworded response cannot silently drop a tool call.
  2. `steps` bounds the whole chain. The old loop bounded only the
     malformed-output resample; the chaining path in main.py had no counter at
     all and would run forever against a cooperative model.
  3. The anomaly rule executes in Python after every scan rather than being
     asked for in the prompt.

`thought` stays a required schema field, so the narration the voice interface
depends on survives the move off the text protocol.

Nodes never speak. They append to `narration`, and exactly one consumer reads
it — the console driver in Phase 1, the event fanout in Phase 2. Letting nodes
call the speaker directly would mean every line is spoken twice the moment the
WebSocket layer lands.

Phase 6 adds one more node, `confirm`, between `reason` and `act`. A skill
whose manifest sets `"destructive": True` routes through it instead of going
straight to `act`; everything else is unaffected — `route_after_reason` only
looks up `destructive` for the action actually chosen, so a turn that never
selects a destructive skill never touches this node at all.
"""
from langgraph.graph import END, StateGraph

from core import intents
from core.config import SETTINGS
from core.nodes.act import act_node
from core.nodes.anomaly_guard import anomaly_guard_node
from core.nodes.conclude import conclude_node
from core.nodes.confirm import confirm_node
from core.nodes.converse import converse_node
from core.nodes.reason import reason_node
from core.registry import NO_ACTION
from core.small_talk import is_small_talk
from core.state import AgentState

# How many back-to-back identical tool calls a turn may make before the graph
# stops it. Two is one more chance than none: the first repeat gets a pointed
# observation and a chance to answer, the second ends the chain.
REPEAT_LIMIT = 2

# Tool calls a single turn may execute before it must answer from what it has.
# Deliberately far below max_react_steps (12), which counts reasoning passes and
# so permits a dozen tool calls before tripping. Measured on 2026-08-04: a
# wandering turn spent 12 calls and 37 seconds and produced nothing. Five is
# comfortably more than any legitimate chain observed in this project — the
# longest real one is search, read, summarise.
TOOL_CALL_BUDGET = 5

# Consecutive calls to the same tool, whatever the parameters. Three is a
# generous reading of "retrying with a correction"; nine in a row, which is what
# was observed, is a model hunting for a result that is not there.
SAME_TOOL_LIMIT = 3


def route_after_reason(state: AgentState, active_skills: dict | None = None) -> str:
    # `action` is tested BEFORE `final_answer`, and that order is not arbitrary.
    # The schema requires final_answer, so the model fills it even when an
    # action is pending — observed in practice as a placeholder answer
    # ("I can see the following: [list of environment details]") returned
    # alongside a correct scan_environment call. Reading final_answer first
    # would speak the placeholder and never run the tool.
    #
    # `active_skills` defaults to None/{} so existing single-argument callers
    # (tests/test_placeholder_answer.py calls this directly) keep working: an
    # unknown or absent skill is simply never treated as destructive here,
    # matching build_graph's own default-deny stance one layer up.
    active_skills = active_skills or {}
    if state.get("action") and state["action"] != NO_ACTION:
        if state.get("steps", 0) >= SETTINGS["llm"]["max_react_steps"]:
            return "abort"
        skill = active_skills.get(state["action"])
        if skill is not None and skill.manifest.get("destructive"):
            return "confirm"
        return "act"
    if state.get("final_answer"):
        return END
    if state.get("steps", 0) >= SETTINGS["llm"]["max_react_steps"]:
        return "abort"
    # Declined to act and gave no answer. Rare now that all fields are
    # required, but still legal — nudge, and let the step bound catch a loop.
    return "nudge"


def route_after_confirm(state: AgentState) -> str:
    # confirm_node sets this explicitly on both the approved and denied
    # paths, so a missing key (which .get would treat the same as False)
    # would only ever come from a bug in that node, not a legitimate state.
    return "act" if state.get("action_approved") else "reason"


def finish_node(state: AgentState) -> dict:
    """End the turn with the observation as the answer.

    Reached only from a skill whose manifest declares `terminal`, meaning its
    output is the complete reply and there is nothing left to reason about.
    """
    return {"final_answer": state.get("observation", "")}


def route_after_act(state: AgentState, active_skills: dict | None = None) -> str:
    # Only scan_environment (the webcam skill) produces detections worth
    # guarding on. This is deliberately webcam-specific, not just an
    # oversight to extend later (open item 2, carried from the Phase 4
    # plan): the screen watcher added in Phase 4 (vision/watcher.py) is not
    # a graph action at all. It runs on its own background thread outside
    # any turn, and it writes prose into state["screen_context"], never
    # counts into state["detections"] — there is nothing shaped like an
    # anomaly for this guard to see from it. If a future skill produces
    # person/object counts from something other than the webcam, add it to
    # this check explicitly rather than widening it speculatively now.
    # A terminal skill's observation is the answer. Enforced here rather than
    # asked for in the prompt, because asking did not work: given a complete
    # answer from core_identity, the model kept going for eleven more steps,
    # drafting documents and taking a webcam photo that muted the machine's
    # audio, before hitting the step bound. The graph can simply not offer it
    # the opportunity.
    skill = (active_skills or {}).get(state.get("action"))
    if skill is not None and skill.manifest.get("terminal"):
        return "finish"

    # Two identical repeats in one turn means the chain has stopped making
    # progress. Observed live on 2026-08-04: the repeat guard fired, the model
    # answered by choosing a *different* unrelated tool, that repeated too, and
    # the turn ran on for twenty steps before ending with a list of every tool
    # loaded. The instruction to answer now is in act.py's observation and it was
    # not enough — with 45 tools there are 45 ways to avoid concluding. So the
    # graph ends the chain and lets `finish` speak what is already known, exactly
    # as it does for a terminal skill.
    if state.get("repeated_calls", 0) >= REPEAT_LIMIT:
        return "finish"

    # The webcam guard runs before any budget check, because a scan that has
    # already happened must still be assessed — the privacy rule is not
    # something to skip because the turn is over budget.
    if state.get("action") == "scan_environment":
        return "anomaly_guard"

    # Out of tool budget, or stuck on one tool: stop choosing and answer. Routed
    # to `conclude` rather than `finish` because the last observation is usually
    # not the answer here — unlike a terminal skill, where it is.
    if (state.get("tool_calls", 0) >= TOOL_CALL_BUDGET
            or state.get("same_tool_streak", 0) >= SAME_TOOL_LIMIT):
        return "conclude"

    return "reason"


def dispatch_node(state: AgentState, active_skills: dict) -> dict:
    """Enter the loop with the tool already chosen, for questions about FRIDAY itself.

    Returns the same shape `reason_node` does, so everything downstream — the
    confirmation lookup, `act`, the `terminal` flag, the step count — behaves
    exactly as it would had the model picked this tool. `messages` is left
    untouched: if the chosen skill is not terminal and the turn continues, the
    next `reason` step builds the message list from scratch, which is already
    what it does on the first pass.

    See core/intents.py for the measurement that made this a node rather than a
    prompt rule. `thought` is written here rather than generated because it is
    spoken aloud before the tool runs, and a plan this specific does not need a
    model to phrase it.
    """
    routed = intents.route(state.get("user_input", ""), active_skills)
    if routed is None:                       # unreachable via the entry point
        return {"steps": state.get("steps", 0) + 1}
    skill, params = routed
    return {
        "thought": DISPATCH_THOUGHTS.get(skill, "Let me check."),
        "action": skill,
        "action_input": params,
        "final_answer": "",
        "steps": state.get("steps", 0) + 1,
    }


# One spoken line per deterministic route. Said before the skill runs, so each
# is a plan and none of them previews the answer — the same rule the model's own
# `thought` is held to.
DISPATCH_THOUGHTS = {
    "core_identity": "Let me tell you what I actually have loaded rather than guess.",
    "explain_architecture": "I will read that out of my own architecture notes.",
    "explain_last_turn": "Let me look at what I actually did.",
    "diagnose_self": "Let me run through my own state and see.",
    "skill_health": "Let me check which of my skills actually loaded.",
}


def nudge_node(state: AgentState) -> dict:
    return {"messages": [*state["messages"], {"role": "user", "content":
            "That response had no action and no final answer. Either choose an "
            "action or give the final answer now."}]}


def abort_node(state: AgentState) -> dict:
    # A plain answer rather than a traceback: the failure surface is a voice.
    # Returned as `final_answer` only — the driver speaks that on its own, and
    # putting it in `narration` too would say it twice.
    return {"final_answer": "I worked through several steps without reaching a conclusion, so I stopped."}


def _entry_point(active_skills: dict):
    """Which node a fresh turn starts at. A plain function of the text.

    Kept out of build_graph as a named function so it can be tested without
    compiling a graph, and so the ordering — conversation, then self-knowledge,
    then the model — is written down once in something readable.
    """
    def choose(state: AgentState) -> str:
        text = state.get("user_input", "")
        if is_small_talk(text):
            return "converse"
        if intents.route(text, active_skills) is not None:
            return "dispatch"
        return "reason"
    return choose


def build_graph(active_skills: dict, confirm=None):
    """Compile the graph. `confirm` is injected exactly like `emit` elsewhere
    in this project: the node that needs it (confirm_node) takes it as a
    plain argument rather than reaching for a global, so a caller that wires
    nothing gets the safe default rather than a NameError or a silent bypass.

    `confirm(action: str, action_input: dict, thought: str) -> bool` is
    called synchronously from whichever thread is running the turn. Leaving
    it `None` — the default — means every destructive action is denied; see
    core/nodes/confirm.py for why that has to be the default rather than an
    opt-in.
    """
    graph = StateGraph(AgentState)
    graph.add_node("converse", converse_node)
    graph.add_node("dispatch", lambda s: dispatch_node(s, active_skills))
    graph.add_node("conclude", conclude_node)
    graph.add_node("reason", lambda s: reason_node(s, active_skills))
    graph.add_node("confirm", lambda s: confirm_node(s, confirm))
    graph.add_node("act", lambda s: act_node(s, active_skills))
    graph.add_node("anomaly_guard", lambda s: anomaly_guard_node(s, active_skills))
    graph.add_node("nudge", nudge_node)
    graph.add_node("finish", finish_node)
    graph.add_node("abort", abort_node)

    # A greeting does not enter the reasoning loop at all. `reason` hands the
    # model a schema whose `action` is an enum of every loaded tool and is a
    # required field, so for "hello" it is being asked which of 45 things to do
    # when the answer is to say hello back. `converse` calls the model with no
    # schema and no tool list, so a tool call is not something it can produce.
    # See core/small_talk.py for the live transcript that forced this.
    #
    # `dispatch` is the second such gate, for questions about FRIDAY itself. It
    # is checked after small talk and before the model, because the model
    # answers those from a generic self-image it is certain of and measurably
    # wrong about — see core/intents.py for the two benchmark runs that
    # established it. A message matching neither gate takes the ordinary path.
    graph.set_conditional_entry_point(
        _entry_point(active_skills),
        {"converse": "converse", "dispatch": "dispatch", "reason": "reason"},
    )
    graph.add_edge("converse", END)
    # Same edges as `reason`: a dispatched action is an action, and every gate
    # that stands in front of a chosen tool has to stand in front of this one
    # too. Nothing here is destructive today, and routing it through the same
    # conditional is what keeps that from mattering if something ever is.
    graph.add_conditional_edges("dispatch", lambda s: route_after_reason(s, active_skills),
                                {"act": "act", "confirm": "confirm", "nudge": "nudge",
                                 "abort": "abort", END: END})
    graph.add_conditional_edges("reason", lambda s: route_after_reason(s, active_skills),
                                {"act": "act", "confirm": "confirm", "nudge": "nudge",
                                 "abort": "abort", END: END})
    graph.add_conditional_edges("confirm", route_after_confirm, {"act": "act", "reason": "reason"})
    graph.add_conditional_edges("act", lambda s: route_after_act(s, active_skills),
                                {"anomaly_guard": "anomaly_guard", "reason": "reason",
                                 "finish": "finish", "conclude": "conclude"})
    graph.add_edge("conclude", END)
    graph.add_edge("anomaly_guard", "reason")
    graph.add_edge("nudge", "reason")
    graph.add_edge("finish", END)
    graph.add_edge("abort", END)
    return graph.compile()
