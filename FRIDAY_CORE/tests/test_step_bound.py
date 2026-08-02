# tests/test_step_bound.py
"""A model mock that always acts must still be stopped by the step bound."""
import json

from core.config import SETTINGS
from core.graph import build_graph


class _FakeSkill:
    def __init__(self):
        self.manifest = {
            "name": "always_act",
            "description": "a skill that always succeeds, used only for the step-bound test",
            "parameters": [],
        }

    def execute(self, params=None):
        return {"status": "success", "message": "ok"}


def test_step_bound_reaches_abort(monkeypatch):
    monkeypatch.setitem(SETTINGS["llm"], "max_react_steps", 2)

    graph = build_graph({"always_act": _FakeSkill()})

    monkeypatch.setattr(
        "core.llm_client.chat",
        lambda *a, **k: json.dumps({
            "thought": "Acting again.",
            "action": "always_act",
            "action_input": {},
            "final_answer": "",
        }),
    )

    result = graph.invoke({"user_input": "loop forever", "messages": [], "steps": 0},
                           {"recursion_limit": 40})

    assert result["final_answer"] == "I worked through several steps without reaching a conclusion, so I stopped."
