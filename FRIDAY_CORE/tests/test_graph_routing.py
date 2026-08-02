# tests/test_graph_routing.py
"""Graph routing against fake skills and a mocked llm_client, no model needed."""
import json

from core.graph import build_graph


class _FakeSkill:
    def __init__(self, name, message="ok"):
        self.manifest = {"name": name, "description": "a fake skill used only for routing tests", "parameters": []}
        self.message = message
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": self.message}


def _decision(thought="", action="none", action_input=None, final_answer=""):
    return json.dumps({
        "thought": thought,
        "action": action,
        "action_input": action_input or {},
        "final_answer": final_answer,
    })


def test_action_routes_to_act(monkeypatch):
    echo = _FakeSkill("echo", message="echoed")
    graph = build_graph({"echo": echo})

    responses = iter([
        _decision(thought="I will echo.", action="echo", action_input={}),
        _decision(thought="Done.", action="none", final_answer="all set"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = graph.invoke({"user_input": "echo something", "messages": [], "steps": 0},
                           {"recursion_limit": 40})

    assert echo.calls == 1
    assert result["final_answer"] == "all set"


def test_final_answer_routes_to_end(monkeypatch):
    graph = build_graph({})

    monkeypatch.setattr(
        "core.llm_client.chat",
        lambda *a, **k: _decision(thought="No tool needed.", action="none", final_answer="direct answer"),
    )

    result = graph.invoke({"user_input": "hi", "messages": [], "steps": 0}, {"recursion_limit": 40})

    assert result["final_answer"] == "direct answer"
    assert result["steps"] == 1


def test_unknown_tool_produces_clean_observation(monkeypatch):
    graph = build_graph({})

    responses = iter([
        _decision(thought="Calling a stale tool.", action="ghost_tool", action_input={}),
        _decision(thought="Recovered.", action="none", final_answer="handled it"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = graph.invoke({"user_input": "do something", "messages": [], "steps": 0},
                           {"recursion_limit": 40})

    assert result["observation"] == "The tool 'ghost_tool' is not loaded."
    assert result["final_answer"] == "handled it"
