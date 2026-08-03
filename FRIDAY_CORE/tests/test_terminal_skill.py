# tests/test_terminal_skill.py
"""A skill whose output is the whole answer ends the turn.

The failure this exists for, observed live: asked "what are your abilities?",
the model called core_identity, received a complete answer, and then kept
going for eleven more steps — drafting two documents, writing two memory
entries, taking a webcam photo that tripped the privacy guard and muted the
machine's audio, and fetching unrelated pages — before hitting the step bound
and giving up with "I worked through several steps without reaching a
conclusion".

The prompt already asked it to stop. This is the graph not offering it the
chance, which is the same reasoning as the anomaly guard: enforcement that
does not depend on the model agreeing.
"""
import json

from core.graph import build_graph


class _TerminalSkill:
    def __init__(self):
        self.manifest = {
            "name": "identity",
            "description": "a fake terminal skill used only for routing tests",
            "parameters": [],
            "terminal": True,
        }
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "I am a local assistant with 17 skills."}


class _OrdinarySkill:
    def __init__(self, name="scratch"):
        self.manifest = {"name": name, "description": "a fake ordinary skill", "parameters": []}
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "did a thing"}


def _decision(action="none", action_input=None, final_answer="", thought=""):
    return json.dumps({
        "thought": thought,
        "action": action,
        "action_input": action_input or {},
        "final_answer": final_answer,
    })


def _run(graph, text="what can you do?"):
    return graph.invoke({"user_input": text, "messages": [], "steps": 0}, {"recursion_limit": 40})


def test_a_terminal_skills_output_becomes_the_answer(monkeypatch):
    identity = _TerminalSkill()
    graph = build_graph({"identity": identity})

    # Only one decision is ever supplied. If the graph asked the model a
    # second time, next() would raise StopIteration and this test would error
    # rather than quietly pass — which is the point.
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: _decision(action="identity"))

    result = _run(graph)

    assert identity.calls == 1
    assert result["final_answer"] == "I am a local assistant with 17 skills."


def test_no_further_tools_run_after_a_terminal_skill(monkeypatch):
    # The live failure was not a missing answer, it was everything that
    # happened afterwards: files written and a webcam photo taken for a
    # question that wanted one sentence.
    identity = _TerminalSkill()
    scratch = _OrdinarySkill()
    graph = build_graph({"identity": identity, "scratch": scratch})

    responses = iter([
        _decision(action="identity"),
        # A model that wants to keep going. It never gets asked.
        _decision(action="scratch"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    _run(graph)

    assert scratch.calls == 0


def test_an_ordinary_skill_still_loops_back_to_reason(monkeypatch):
    # The guard must not become "every tool ends the turn" — chaining is the
    # whole point of the graph.
    scratch = _OrdinarySkill()
    graph = build_graph({"scratch": scratch})

    responses = iter([
        _decision(action="scratch"),
        _decision(action="none", final_answer="all done"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert scratch.calls == 1
    assert result["final_answer"] == "all done"
