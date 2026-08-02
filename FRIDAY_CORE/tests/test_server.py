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
    yield
    server_app._memory_buffer.clear()
    server_app._busy = False
    server_app._connections.clear()
    server_app.interrupter.reset()


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
