# tests/test_conversation_fast_path.py
"""A greeting must not enter the reasoning loop, and a stuck chain must end.

Both of these are regressions from one live transcript on 2026-08-04, with 45
skills loaded. "Hello, friday." produced: describe_screen, describe_screen again
(refused as a repeat), read_webpage, read_webpage again (refused), three
web_searches, then core_identity — and answered with a list of all 44 other tools.
Twenty-odd steps for a greeting.

Every step broke a rule already written in the system prompt. That is the point of
these tests: the fix is not a better-worded rule, it is the graph making the wrong
move unavailable, so the tests assert on structure — that no tool was reachable, and
that the chain stopped — rather than on how the model happens to behave today.
"""
import pytest
from core import graph as graph_module
from core.graph import REPEAT_LIMIT, route_after_act
from core.nodes.act import act_node
from core.nodes.converse import MAX_WORDS, converse_node
from core.small_talk import is_small_talk

# --- what counts as conversation -----------------------------------------


@pytest.mark.parametrize("message", [
    "Hello, friday.",
    "hello",
    "Hi",
    "hey there",
    "Good morning",
    "good evening friday",
    "thanks",
    "Thank you very much",
    "thanks a lot",
    "ok",
    "okay, cool",
    "nice",
    "how are you",
    "How are you doing today?",
    "hows it going",
    "are you there?",
    "yo",
    "namaste",
    "sup",
    "bye",
    "goodnight",
    "hello again",
])
def test_pure_conversation_is_recognised(message):
    assert is_small_talk(message) is True, f"{message!r} should have skipped the tool loop"


@pytest.mark.parametrize("message", [
    # The dangerous class: a greeting with a real request attached. Matching
    # these would make the request unanswerable, which is worse than the bug.
    "hi, what's the weather?",
    "hello, read me that document",
    "thanks, now delete that file",
    "ok remind me at 5pm",
    "hey, what can you do?",
    "good morning, any news?",
    "hello, how do you work?",
    "who are you",
    "what are your abilities",
    "cool, run the tests",
    "morning — is the camera up?",
    "translate this into Hindi",
    "hi, take a screenshot",
    "how are the tests doing",
    "what's on my calendar",
    "nice, open that url",
])
def test_a_request_wrapped_in_pleasantries_is_not_small_talk(message):
    assert is_small_talk(message) is False, f"{message!r} contains a real request and must reach a tool"


@pytest.mark.parametrize("message", ["", "   ", "\n"])
def test_an_empty_message_is_not_small_talk(message):
    """Empty input is a different problem and the ordinary path reports it."""
    assert is_small_talk(message) is False


def test_a_long_message_is_never_small_talk():
    """Whatever words it uses, thirteen of them are doing something."""
    assert is_small_talk(" ".join(["hello"] * 13)) is False


# --- the conversational reply --------------------------------------------


def test_converse_answers_without_choosing_a_tool(monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: "Good morning, Sir.")

    result = converse_node({"user_input": "hello", "steps": 0})

    assert result["final_answer"] == "Good morning, Sir."
    assert "action" not in result, "the conversational path must not be able to emit an action"


def test_converse_is_given_no_tool_schema(monkeypatch):
    """The structural fix: with no schema there is no action field to fill in."""
    captured = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return "Hello, Sir."

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    converse_node({"user_input": "hi", "steps": 0})

    assert captured["kwargs"].get("fmt") is None
    system = captured["messages"][0]["content"]
    # None of the tool names should be in front of the model at all.
    for tool in ("describe_screen", "core_identity", "web_search", "run_command"):
        assert tool not in system


def test_the_reply_does_not_list_capabilities(monkeypatch):
    """The exact observed failure: "hello" answered with an inventory of tools."""
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: "Hello, Sir.")

    result = converse_node({"user_input": "Hello, friday.", "steps": 0})

    assert "skills loaded" not in result["final_answer"]
    assert "describe_screen" not in result["final_answer"]


def test_an_unreachable_model_still_produces_a_greeting(monkeypatch):
    """A greeting must never surface a traceback."""
    def explode(*args, **kwargs):
        raise ConnectionError("ollama is not running")

    monkeypatch.setattr("core.llm_client.chat", explode)

    result = converse_node({"user_input": "hello", "steps": 0})

    assert result["final_answer"]
    assert "Traceback" not in result["final_answer"]
    assert "ConnectionError" not in result["final_answer"]


def test_a_rambling_reply_is_trimmed(monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: " ".join(["word"] * 200))

    result = converse_node({"user_input": "hi", "steps": 0})

    assert len(result["final_answer"].split()) <= MAX_WORDS + 1


@pytest.mark.parametrize("raw, expected", [
    ('"Hello, Sir."', "Hello, Sir."),
    ("FRIDAY: Hello, Sir.", "Hello, Sir."),
    ("Assistant: Good evening.", "Good evening."),
    ("  Hello, Sir.  ", "Hello, Sir."),
])
def test_model_wrappers_are_stripped(monkeypatch, raw, expected):
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: raw)

    assert converse_node({"user_input": "hi", "steps": 0})["final_answer"] == expected


# --- the graph routes a greeting away from the tool loop -----------------


def test_the_entry_point_sends_a_greeting_to_converse(monkeypatch):
    """Asserted through the compiled graph, so a rewiring cannot silently undo it."""
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: "Hello, Sir.")

    class _Skill:
        manifest = {"name": "web_search", "description": "x" * 50, "parameters": []}

        def execute(self, params=None):
            raise AssertionError("a greeting reached a tool")

    compiled = graph_module.build_graph({"web_search": _Skill()})
    final = compiled.invoke({"user_input": "hello", "messages": [], "steps": 0})

    assert final["final_answer"] == "Hello, Sir."


def test_a_real_request_still_reaches_the_reasoning_loop(monkeypatch):
    """The fast path must not swallow work."""
    seen = []

    def fake_reason(state, active_skills):
        seen.append(state["user_input"])
        return {"action": "none", "final_answer": "42", "steps": 1,
                "messages": state["messages"]}

    monkeypatch.setattr(graph_module, "reason_node", fake_reason)
    compiled = graph_module.build_graph({})
    compiled.invoke({"user_input": "what is the weather in Delhi", "messages": [], "steps": 0})

    assert seen == ["what is the weather in Delhi"]


# --- a stuck chain ends --------------------------------------------------


class _CountingSkill:
    manifest = {"name": "web_search", "description": "x" * 50, "parameters": ["q"]}

    def __init__(self):
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "the same answer as before"}


def test_the_first_identical_repeat_is_refused_without_rerunning():
    skill = _CountingSkill()
    state = {"action": "web_search", "action_input": {"q": "x"}, "messages": [],
             "last_call": 'web_search:{"q": "x"}'}

    result = act_node(state, {"web_search": skill})

    assert skill.calls == 0
    assert result["repeated_calls"] == 1
    assert "Do not call it again" in result["observation"]


def test_the_second_repeat_says_not_to_pick_another_tool_either():
    """Observed: after the first refusal the model chose a different tool instead."""
    skill = _CountingSkill()
    state = {"action": "web_search", "action_input": {"q": "x"}, "messages": [],
             "last_call": 'web_search:{"q": "x"}', "repeated_calls": 1}

    result = act_node(state, {"web_search": skill})

    assert result["repeated_calls"] == 2
    assert "Do not choose another tool either" in result["observation"]


def test_the_graph_ends_the_chain_after_the_repeat_limit():
    state = {"action": "web_search", "repeated_calls": REPEAT_LIMIT}

    assert route_after_act(state, {}) == "finish"


def test_one_repeat_does_not_end_the_chain():
    """The first repeat gets a chance to answer; only the second stops it."""
    state = {"action": "web_search", "repeated_calls": 1}

    assert route_after_act(state, {}) == "reason"


def test_a_normal_call_is_unaffected():
    skill = _CountingSkill()
    state = {"action": "web_search", "action_input": {"q": "x"}, "messages": [],
             "last_call": "something:else"}

    result = act_node(state, {"web_search": skill})

    assert skill.calls == 1
    assert "repeated_calls" not in result


# --- the tool budget: a wandering turn must answer, not apologise ---------


def test_the_tool_budget_routes_to_conclude():
    """Measured live: 12 tool calls and 37 seconds produced nothing at all."""
    state = {"action": "web_search", "tool_calls": graph_module.TOOL_CALL_BUDGET}

    assert route_after_act(state, {}) == "conclude"


def test_a_streak_on_one_tool_routes_to_conclude():
    """Observed: nine consecutive manage_settings calls with varying parameters,
    so the identical-repeat guard never fired once."""
    state = {"action": "manage_settings", "same_tool_streak": graph_module.SAME_TOOL_LIMIT}

    assert route_after_act(state, {}) == "conclude"


def test_a_short_legitimate_chain_is_not_cut_off():
    """search, read, summarise is the longest real chain in this project."""
    state = {"action": "read_webpage", "tool_calls": 2, "same_tool_streak": 1}

    assert route_after_act(state, {}) == "reason"


def test_the_privacy_guard_still_runs_when_the_budget_is_spent():
    """A scan that happened must be assessed; being over budget is not a reason
    to skip the privacy rule."""
    state = {"action": "scan_environment", "tool_calls": 99, "same_tool_streak": 99}

    assert route_after_act(state, {}) == "anomaly_guard"


def test_act_counts_tool_calls_and_streaks():
    skill = _CountingSkill()
    first = act_node({"action": "web_search", "action_input": {"q": "a"}, "messages": []},
                     {"web_search": skill})
    second = act_node({"action": "web_search", "action_input": {"q": "b"}, "messages": [],
                       "tool_calls": first["tool_calls"], "same_tool_streak": first["same_tool_streak"],
                       "last_tool": first["last_tool"], "last_call": first["last_call"]},
                      {"web_search": skill})

    assert first["tool_calls"] == 1
    assert second["tool_calls"] == 2
    assert second["same_tool_streak"] == 2, "different parameters, same tool, so the streak grows"


def test_a_different_tool_resets_the_streak():
    skill = _CountingSkill()
    result = act_node({"action": "web_search", "action_input": {"q": "a"}, "messages": [],
                       "same_tool_streak": 2, "last_tool": "describe_screen"},
                      {"web_search": skill})

    assert result["same_tool_streak"] == 1


def test_conclude_answers_without_a_tool_schema(monkeypatch):
    from core.nodes.conclude import conclude_node

    captured = {}

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return "36."

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    result = conclude_node({"user_input": "what is 15 percent of 240", "messages": []})

    assert result["final_answer"] == "36."
    assert captured["kwargs"].get("fmt") is None
    assert "action" not in result


def test_conclude_is_shown_the_observations_it_already_has(monkeypatch):
    from core.nodes.conclude import conclude_node

    captured = {}
    monkeypatch.setattr("core.llm_client.chat",
                        lambda messages, **k: captured.setdefault("m", messages) and "" or "answer")
    conclude_node({
        "user_input": "what did the page say",
        "messages": [{"role": "user", "content": "Observation: the page said 31 degrees"},
                     {"role": "assistant", "content": "irrelevant"}],
    })

    assert "31 degrees" in captured["m"][1]["content"]


def test_conclude_never_surfaces_a_traceback(monkeypatch):
    from core.nodes.conclude import conclude_node

    def explode(*args, **kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr("core.llm_client.chat", explode)
    result = conclude_node({"user_input": "anything", "messages": []})

    assert result["final_answer"]
    assert "ConnectionError" not in result["final_answer"]
