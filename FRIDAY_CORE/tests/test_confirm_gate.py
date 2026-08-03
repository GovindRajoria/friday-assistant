# tests/test_confirm_gate.py
"""The confirmation gate, exercised through the whole graph rather than by
calling confirm_node directly.

Testing the node in isolation would prove the node returns the right dict and
prove nothing about the thing that actually matters: that a destructive skill
does not run. The routing edge between `reason` and `confirm` is half the
gate, so every assertion here is on `skill.calls` after a real graph
invocation — the number of times the dangerous code was reached.
"""
import json

from core.graph import build_graph


class _DestroyerSkill:
    """Stands in for manage_files / send_keys: declares itself destructive."""

    def __init__(self):
        self.manifest = {
            "name": "destroyer",
            "description": "a fake destructive skill used only for gate tests",
            "parameters": ["target"],
            "destructive": True,
        }
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "destroyed"}


class _HarmlessSkill:
    def __init__(self):
        self.manifest = {
            "name": "harmless",
            "description": "a fake non-destructive skill used only for gate tests",
            "parameters": [],
        }
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "nothing happened"}


class _RecordingConfirm:
    """A confirm callable that answers a fixed way and remembers being asked.

    Asserting on `calls` rather than only on the skill's own call count is
    what distinguishes "the gate approved it" from "the gate was never
    consulted" — both leave a non-destructive skill executed once.
    """

    def __init__(self, answer: bool):
        self.answer = answer
        self.calls = []

    def __call__(self, action, action_input, thought):
        self.calls.append((action, action_input, thought))
        return self.answer


def _decision(thought="", action="none", action_input=None, final_answer=""):
    return json.dumps({
        "thought": thought,
        "action": action,
        "action_input": action_input or {},
        "final_answer": final_answer,
    })


def _run(graph, user_input="do the thing"):
    return graph.invoke({"user_input": user_input, "messages": [], "steps": 0}, {"recursion_limit": 40})


def test_no_confirm_callable_denies_and_the_skill_never_runs(monkeypatch):
    # The most important test in this phase. Every new integration point
    # (a second server, a scheduled task, a test harness) that forgets to
    # wire a confirm callable must get a refusal, not an open door.
    destroyer = _DestroyerSkill()
    graph = build_graph({"destroyer": destroyer})  # no confirm= on purpose

    responses = iter([
        _decision(thought="I will destroy it.", action="destroyer", action_input={"target": "x"}),
        _decision(thought="Understood.", action="none", final_answer="I did not do that."),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert destroyer.calls == 0
    assert result["action_approved"] is False
    assert result["observation"] == "Confirmation denied for 'destroyer'. The action was not performed."


def test_approval_lets_the_destructive_skill_run(monkeypatch):
    destroyer = _DestroyerSkill()
    confirm = _RecordingConfirm(True)
    graph = build_graph({"destroyer": destroyer}, confirm=confirm)

    responses = iter([
        _decision(thought="I will destroy it.", action="destroyer", action_input={"target": "x"}),
        _decision(thought="Done.", action="none", final_answer="destroyed it"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert destroyer.calls == 1
    assert result["observation"] == "destroyed"
    assert result["final_answer"] == "destroyed it"
    # The gate saw the real proposed call, not a summary of it — the HUD and
    # the console both render these three values to the human being asked.
    assert confirm.calls == [("destroyer", {"target": "x"}, "I will destroy it.")]


def test_denial_blocks_the_skill_and_the_turn_still_finishes(monkeypatch):
    # A denial is an Observation, not an abort: the model is told no and gets
    # to say something about it. A turn that ended silently here would leave
    # a socket client waiting for an answer that never came.
    destroyer = _DestroyerSkill()
    confirm = _RecordingConfirm(False)
    graph = build_graph({"destroyer": destroyer}, confirm=confirm)

    responses = iter([
        _decision(thought="I will destroy it.", action="destroyer", action_input={"target": "x"}),
        _decision(thought="Understood.", action="none", final_answer="I left it alone."),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert destroyer.calls == 0
    assert len(confirm.calls) == 1
    assert result["final_answer"] == "I left it alone."
    # The model has to be able to see why nothing happened, or it will simply
    # try again with no idea it was refused.
    assert any("Confirmation denied for 'destroyer'" in message["content"] for message in result["messages"])


def test_a_non_destructive_skill_never_reaches_the_gate(monkeypatch):
    harmless = _HarmlessSkill()
    confirm = _RecordingConfirm(True)
    graph = build_graph({"harmless": harmless}, confirm=confirm)

    responses = iter([
        _decision(thought="This is safe.", action="harmless", action_input={}),
        _decision(thought="Done.", action="none", final_answer="all set"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    result = _run(graph)

    assert harmless.calls == 1
    # Not merely "it was approved" — the human was never interrupted at all.
    assert confirm.calls == []
    assert result["final_answer"] == "all set"


def test_repeatedly_proposing_a_denied_action_still_hits_the_step_bound(monkeypatch):
    # Denial routes back to `reason`, which is a loop. It is bounded by the
    # ordinary step counter and nothing else, so a model that never takes no
    # for an answer must still terminate.
    destroyer = _DestroyerSkill()
    confirm = _RecordingConfirm(False)
    graph = build_graph({"destroyer": destroyer}, confirm=confirm)

    monkeypatch.setattr(
        "core.llm_client.chat",
        lambda *a, **k: _decision(thought="Trying again.", action="destroyer", action_input={"target": "x"}),
    )

    result = _run(graph)

    assert destroyer.calls == 0
    assert result["final_answer"] == "I worked through several steps without reaching a conclusion, so I stopped."
