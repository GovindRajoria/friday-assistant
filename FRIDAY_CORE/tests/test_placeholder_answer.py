# tests/test_placeholder_answer.py
"""Regression test for the observed failure: the schema requires final_answer,
so the model fills it even when an action is pending. Routing must prefer the
action, and the node must discard the placeholder rather than let it reach
state["final_answer"].
"""
import json

from core.graph import build_graph, route_after_reason
from core.nodes.reason import reason_node

PLACEHOLDER = "I can see the following: [list of environment details]"


class _FakeScan:
    def __init__(self):
        self.manifest = {
            "name": "scan_environment",
            "description": "a fake scan skill used only for the placeholder-answer regression test",
            "parameters": [],
        }
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "scanned", "data": {"detections": {"person": 1, "laptop": 1}}}


def _decision_json():
    return json.dumps({
        "thought": "I will check the room.",
        "action": "scan_environment",
        "action_input": {},
        "final_answer": PLACEHOLDER,
    })


def test_route_after_reason_prefers_action_over_placeholder_answer():
    state = {"action": "scan_environment", "final_answer": PLACEHOLDER, "steps": 0}
    assert route_after_reason(state) == "act"


def test_reason_node_discards_placeholder_answer(monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: _decision_json())

    result = reason_node({"user_input": "scan the room"}, {"scan_environment": _FakeScan()})

    assert result["action"] == "scan_environment"
    assert result["final_answer"] == ""


def test_graph_never_surfaces_the_placeholder(monkeypatch):
    scan = _FakeScan()
    graph = build_graph({"scan_environment": scan})

    responses = iter([
        _decision_json(),
        json.dumps({
            "thought": "All clear.",
            "action": "none",
            "action_input": {},
            "final_answer": "One person present, area is normal.",
        }),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = graph.invoke({"user_input": "scan the room", "messages": [], "steps": 0},
                           {"recursion_limit": 40})

    assert scan.calls == 1
    assert result["final_answer"] != PLACEHOLDER
    assert result["final_answer"] == "One person present, area is normal."
