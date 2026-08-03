# tests/test_server.py
"""server/app.py: /health, the /ws event stream, and single-flight rejection.

No model, microphone or speaker is exercised here — `core.llm_client.chat` is
mocked exactly as the graph routing tests mock it, and `server.speak` is
forced off before `server.app` is imported so importing this module never
spins up a real pyttsx3 engine during a test run.
"""
import ipaddress
import json
import threading
import time

import pytest
from core.config import SETTINGS

SETTINGS["server"]["speak"] = False  # no audio, no COM thread, during tests

import server.app as server_app  # noqa: E402
from core.graph import build_graph  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.app import app  # noqa: E402


class _EchoSkill:
    def __init__(self):
        self.manifest = {"name": "echo", "description": "a fake skill used only for server tests", "parameters": []}

    def execute(self, params=None):
        return {"status": "success", "message": "echoed"}


class _DestructiveEchoSkill:
    """Same trivial behaviour as _EchoSkill, but declared destructive.

    Nothing here touches the filesystem — the point of these tests is the
    socket round trip around the gate, not what a real destructive skill
    does once it is allowed through. `calls` is what proves the gate held.
    """

    def __init__(self):
        self.manifest = {
            "name": "wipe",
            "description": "a fake destructive skill used only for server tests",
            "parameters": ["target"],
            "destructive": True,
        }
        self.calls = 0

    def execute(self, params=None):
        self.calls += 1
        return {"status": "success", "message": "wiped"}


@pytest.fixture
def destructive_graph(monkeypatch):
    """Wire the module's graph to a destructive fake, gated by the real callable.

    `server_app.graph` is built once at import time, before any monkeypatch in
    this file has run, so a test that needs a specific confirm callable has to
    rebuild it. Passing `server_app._confirm_via_socket` explicitly — rather
    than relying on the import-time wiring — is what makes the timeout test
    below able to shorten CONFIRMATION_TIMEOUT_SECONDS and have it matter.
    """
    skill = _DestructiveEchoSkill()
    monkeypatch.setattr(server_app, "graph", build_graph({"wipe": skill}, confirm=server_app._confirm_via_socket))
    return skill


def _decision(action="none", thought="", final_answer="", action_input=None):
    return json.dumps({
        "thought": thought,
        "action": action,
        "action_input": action_input or {},
        "final_answer": final_answer,
    })


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Module-level state (busy flag, memory, connections, interrupt flag) must not leak between tests."""
    server_app._memory_buffer.clear()
    server_app._busy = False
    server_app._connections.clear()
    server_app.interrupter.reset()
    # A resolved confirmation left set would let the next test's destructive
    # action through without ever blocking — the exact stale-approval case
    # _PendingConfirmation.reset() exists to prevent at runtime.
    server_app._pending_confirmation.reset()
    yield
    server_app._memory_buffer.clear()
    server_app._busy = False
    server_app._connections.clear()
    server_app.interrupter.reset()
    server_app._pending_confirmation.reset()


def test_health_returns_ok_and_skills():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["skills"], list)


def test_prompt_streams_events_in_order(monkeypatch):
    echo = _EchoSkill()
    monkeypatch.setattr(server_app, "graph", build_graph({"echo": echo}))

    responses = iter([
        _decision(action="echo", thought="I will echo."),
        # Empty thought on the closing turn keeps the event count exact:
        # a non-empty one would add a second "thought" event, which is a
        # correct outcome for a real turn but noise for this test.
        _decision(action="none", thought="", final_answer="all set"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "prompt", "text": "echo something"}))
        received = [json.loads(ws.receive_text()) for _ in range(4)]

    assert [event["type"] for event in received] == ["thought", "action", "observation", "answer"]
    assert received[1]["payload"]["name"] == "echo"
    assert received[2]["payload"]["text"] == "echoed"
    assert received[3]["payload"]["text"] == "all set"


def test_second_concurrent_prompt_is_rejected(monkeypatch):
    monkeypatch.setattr(server_app, "graph", build_graph({}))

    release = threading.Event()

    def _slow_chat(*args, **kwargs):
        # Holds the first turn "in flight" long enough for the second
        # prompt to land on the busy flag instead of racing past it.
        release.wait(timeout=2)
        return _decision(action="none", thought="", final_answer="done")

    monkeypatch.setattr("core.llm_client.chat", _slow_chat)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        ws1.send_text(json.dumps({"type": "prompt", "text": "first"}))
        ws2.send_text(json.dumps({"type": "prompt", "text": "second"}))

        second_event = json.loads(ws2.receive_text())
        assert second_event["type"] == "error"

        release.set()
        first_event = json.loads(ws1.receive_text())
        assert first_event["type"] == "answer"
        assert first_event["payload"]["text"] == "done"


def test_server_host_is_loopback():
    # This is a public repo shipping an agent with OS-automation skills.
    # A non-loopback default arriving later by accident is worth gating on
    # explicitly rather than trusting the config value blindly.
    host = SETTINGS["server"]["host"]
    assert ipaddress.ip_address(host).is_loopback


def test_failed_turn_reports_an_error_and_keeps_the_socket_open(monkeypatch):
    # Ollama not running, or an unreachable llm.host once inference is
    # offloaded, is an ordinary condition rather than an exotic one. Before
    # this was handled the exception propagated out of the endpoint, the
    # connection was torn down, and the client received nothing at all — a
    # HUD would show a closed socket with no way to say why.
    def explode(*args, **kwargs):
        raise ConnectionError("ollama is unreachable")

    monkeypatch.setattr(server_app, "run_turn", explode)

    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "prompt", "text": "anything"})
        event = websocket.receive_json()

        assert event["type"] == "error"
        assert "ollama is unreachable" in event["payload"]["text"]

        # The connection has to survive the failure, and the busy flag has to
        # clear, or one dead turn wedges the server for the whole session.
        assert server_app._busy is False
        websocket.send_json({"type": "prompt", "text": "again"})
        assert websocket.receive_json()["type"] == "error"

    # A failed turn leaves no half-written transcript behind.
    assert server_app._memory_buffer == []


def test_second_prompt_on_the_same_connection_is_also_rejected(monkeypatch):
    # The two-connection test above passed even when the endpoint awaited the
    # turn inline, because two connections are two coroutines racing on the
    # flag. One connection is not: awaiting inline blocked the receive loop, so
    # a second prompt sat in the socket buffer and ran after the first — queued
    # rather than rejected. A HUD is a single connection, so this is the case
    # that actually matters.
    monkeypatch.setattr(server_app, "graph", build_graph({}))

    release = threading.Event()

    def _slow_chat(*args, **kwargs):
        release.wait(timeout=2)
        return _decision(action="none", thought="", final_answer="done")

    monkeypatch.setattr("core.llm_client.chat", _slow_chat)

    with TestClient(app).websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "prompt", "text": "first"})
        websocket.send_json({"type": "prompt", "text": "second"})

        rejection = websocket.receive_json()
        assert rejection["type"] == "error"
        assert "Already processing" in rejection["payload"]["text"]

        release.set()
        assert websocket.receive_json()["type"] == "answer"


def test_cancel_message_ends_a_turn_in_flight(monkeypatch):
    monkeypatch.setattr(server_app, "graph", build_graph({}))

    def _slow_chat(*args, **kwargs):
        # Blocks on the flag itself rather than an event the test sets
        # independently. Waiting on a plain threading.Event that the test
        # thread flips right after sending the cancel message only makes
        # the cancel *likely* to have been processed first, not certain —
        # the receive loop and this worker thread are scheduled
        # independently. Polling the flag makes the ordering causal: this
        # can only return once interrupter.cancel() has actually run.
        for _ in range(200):  # 2s budget at 10ms/poll
            if server_app.interrupter.interrupted:
                break
            time.sleep(0.01)
        return _decision(action="none", thought="thinking", final_answer="done")

    monkeypatch.setattr("core.llm_client.chat", _slow_chat)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "prompt", "text": "first"}))
        ws.send_text(json.dumps({"type": "cancel"}))

        event = json.loads(ws.receive_text())

        # run_turn checks interrupter.interrupted before it processes the
        # first streamed update, so the turn ends there and the override
        # message is the only event of the turn. It has to arrive as
        # `answer`, the terminal type: a client has no other signal that a
        # turn is over, and while this asserted `status` a cancelled turn
        # left the HUD waiting forever for an answer that never came.
        assert event["type"] == "answer"
        assert "override" in event["payload"]["text"].lower()

    # A cancelled turn still has to release single-flight, or the server is
    # wedged for the rest of the session.
    assert server_app._busy is False


def test_screen_watcher_failure_reuses_the_error_event_type():
    # The watcher runs off a background thread with no socket of its own
    # (vision/watcher.py); this is the mapping that gives its failures a
    # visible surface on the HUD instead of the screen context just going
    # stale with nothing said about why. Reusing `error` rather than adding
    # a type keeps tests/test_event_vocabulary_drift.py untouched.
    envelope = server_app._screen_event_envelope("error", "host unreachable")

    assert envelope["type"] == "error"
    assert "host unreachable" in envelope["payload"]["text"]


def test_screen_watcher_description_is_still_screen_context():
    envelope = server_app._screen_event_envelope("description", "a terminal window")

    assert envelope["type"] == "screen_context"
    assert envelope["payload"]["text"] == "a terminal window"


def test_cancel_between_turns_does_not_poison_the_next_prompt(monkeypatch):
    # Regression for the bug this feature is worth testing for: run_turn
    # leaves interrupter.interrupted set after an interrupted turn, and a
    # cancel with nothing running just sets it directly either way. Without
    # a reset at the start of the next turn, that stray flag would end the
    # very next prompt before it produced anything.
    monkeypatch.setattr(server_app, "graph", build_graph({}))
    monkeypatch.setattr(
        "core.llm_client.chat",
        lambda *a, **k: _decision(action="none", thought="", final_answer="done"),
    )

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "cancel"}))
        ws.send_text(json.dumps({"type": "prompt", "text": "hello"}))

        event = json.loads(ws.receive_text())

    assert event["type"] == "answer"
    assert event["payload"]["text"] == "done"


def test_destructive_action_asks_over_the_socket_and_approval_lets_it_run(monkeypatch, destructive_graph):
    responses = iter([
        _decision(action="wipe", thought="", action_input={"target": "scratch.txt"}),
        _decision(action="none", thought="", final_answer="wiped it"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "prompt", "text": "wipe scratch.txt"})

        # `action` arrives first, before the gate is ever reached. Under
        # stream_mode="updates" the reason node's update — which is what
        # carries the chosen action — is emitted the moment that node
        # finishes, and only then does the graph advance into `confirm`. So
        # the HUD sees the proposed call twice: once as the ordinary action
        # line every turn produces, then again as the thing it is being asked
        # about. Do not "fix" this ordering in a test by reordering the
        # asserts on the assumption the gate comes first.
        assert ws.receive_json()["type"] == "action"

        asked = ws.receive_json()
        # The HUD gets the real proposed call, not a sentence about it — an
        # operator cannot meaningfully approve what they cannot see.
        assert asked["type"] == "confirmation_required"
        assert asked["payload"] == {"name": "wipe", "input": {"target": "scratch.txt"}}

        ws.send_json({"type": "confirm", "approved": True})

        observation = ws.receive_json()
        assert observation["type"] == "observation"
        assert observation["payload"]["text"] == "wiped"
        assert ws.receive_json()["payload"]["text"] == "wiped it"

    assert destructive_graph.calls == 1


def test_denial_over_the_socket_stops_the_skill_and_the_turn_still_answers(monkeypatch, destructive_graph):
    responses = iter([
        _decision(action="wipe", thought="", action_input={"target": "scratch.txt"}),
        _decision(action="none", thought="", final_answer="left it alone"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "prompt", "text": "wipe scratch.txt"})
        assert ws.receive_json()["type"] == "action"
        assert ws.receive_json()["type"] == "confirmation_required"

        ws.send_json({"type": "confirm", "approved": False})

        denial = ws.receive_json()
        assert denial["type"] == "observation"
        assert "Confirmation denied for 'wipe'" in denial["payload"]["text"]
        assert ws.receive_json()["payload"]["text"] == "left it alone"

    assert destructive_graph.calls == 0


def test_an_unanswered_confirmation_times_out_into_a_denial(monkeypatch, destructive_graph):
    # The operator who walks away, or a HUD that closed mid-turn. Without a
    # timeout the worker thread blocks on the event forever and single-flight
    # never releases, so the server is wedged for the rest of the process —
    # which is why the assertion on _busy at the end matters as much as the
    # denial itself.
    monkeypatch.setattr(server_app, "CONFIRMATION_TIMEOUT_SECONDS", 0.1)

    responses = iter([
        _decision(action="wipe", thought="", action_input={"target": "scratch.txt"}),
        _decision(action="none", thought="", final_answer="nothing was done"),
    ])
    monkeypatch.setattr("core.llm_client.chat", lambda *a, **k: next(responses))

    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "prompt", "text": "wipe scratch.txt"})
        assert ws.receive_json()["type"] == "action"
        assert ws.receive_json()["type"] == "confirmation_required"

        # Deliberately answer nothing at all.
        denial = ws.receive_json()
        assert denial["type"] == "observation"
        assert "Confirmation denied for 'wipe'" in denial["payload"]["text"]
        assert ws.receive_json()["payload"]["text"] == "nothing was done"

    assert destructive_graph.calls == 0
    assert server_app._busy is False
