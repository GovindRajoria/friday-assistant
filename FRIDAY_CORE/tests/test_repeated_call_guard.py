# tests/test_repeated_call_guard.py
"""A tool call repeated back-to-back is answered, not re-run.

Observed live before this guard existed: one "summarise today's news" request
made three identical fetches of the same feed before the model was willing to
answer from a result it already had. Every extra call is a network round trip
and several seconds, and produces output identical to what is already in the
transcript.

Asserted on `skill.calls` — how many times the work was actually done — since
that is the cost being avoided.
"""
import json

from core.graph import build_graph


class _CountingSkill:
    def __init__(self, name="lookup", message="the result"):
        self.manifest = {"name": name, "description": "a fake skill used only for repeat tests", "parameters": ["q"]}
        self.message = message
        self.calls = []

    def execute(self, params=None):
        self.calls.append(params or {})
        return {"status": "success", "message": self.message}


def _decision(thought="", action="none", action_input=None, final_answer=""):
    return json.dumps({
        "thought": thought,
        "action": action,
        "action_input": action_input or {},
        "final_answer": final_answer,
    })


def _run(graph):
    return graph.invoke({"user_input": "ask", "messages": [], "steps": 0}, {"recursion_limit": 40})


def test_an_identical_repeat_is_not_executed_again(monkeypatch):
    skill = _CountingSkill()
    graph = build_graph({"lookup": skill})

    responses = iter([
        _decision(action="lookup", action_input={"q": "news"}),
        _decision(action="lookup", action_input={"q": "news"}),   # the same call again
        _decision(action="none", final_answer="here is the summary"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert len(skill.calls) == 1
    assert result["final_answer"] == "here is the summary"


def test_the_repeat_tells_the_model_to_use_what_it_has(monkeypatch):
    # The observation has to be actionable. Silence, or repeating the previous
    # result verbatim, leaves the model in exactly the state that made it loop.
    skill = _CountingSkill()
    graph = build_graph({"lookup": skill})

    responses = iter([
        _decision(action="lookup", action_input={"q": "news"}),
        _decision(action="lookup", action_input={"q": "news"}),
        _decision(action="none", final_answer="done"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert "already ran lookup" in result["observation"]
    assert "use that result to answer now" in result["observation"].lower()


def test_parameter_order_does_not_make_a_call_look_new(monkeypatch):
    # The model does not emit its parameters in a fixed order, and two dicts
    # differing only in key order are the same call.
    skill = _CountingSkill()
    graph = build_graph({"lookup": skill})

    responses = iter([
        _decision(action="lookup", action_input={"q": "news", "count": 8}),
        _decision(action="lookup", action_input={"count": 8, "q": "news"}),
        _decision(action="none", final_answer="done"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    _run(graph)

    assert len(skill.calls) == 1


def test_a_different_argument_still_runs(monkeypatch):
    # The guard must not become "one call per tool per turn". Asking about a
    # second topic is a genuinely different question.
    skill = _CountingSkill()
    graph = build_graph({"lookup": skill})

    responses = iter([
        _decision(action="lookup", action_input={"q": "news"}),
        _decision(action="lookup", action_input={"q": "weather"}),
        _decision(action="none", final_answer="done"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    _run(graph)

    assert [call["q"] for call in skill.calls] == ["news", "weather"]


def test_a_call_repeated_after_a_different_one_runs_again(monkeypatch):
    # Only a back-to-back repeat is certainly redundant. Re-reading a file
    # after writing it, or re-scanning after something changed, is legitimate.
    first = _CountingSkill("first")
    second = _CountingSkill("second")
    graph = build_graph({"first": first, "second": second})

    responses = iter([
        _decision(action="first", action_input={"q": "a"}),
        _decision(action="second", action_input={"q": "b"}),
        _decision(action="first", action_input={"q": "a"}),
        _decision(action="none", final_answer="done"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    _run(graph)

    assert len(first.calls) == 2
    assert len(second.calls) == 1
