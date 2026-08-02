# tests/test_anomaly_guard.py
"""Table-driven coverage of the anomaly rule. No model, camera or microphone involved."""
from core.nodes.anomaly_guard import anomaly_guard_node


class _FakeMediaControl:
    def __init__(self):
        self.calls = []

    def execute(self, params=None):
        action = (params or {}).get("action")
        self.calls.append(action)
        return {"status": "success", "message": f"{action} applied"}


def _state(detections, anomaly_active=False):
    return {"detections": detections, "anomaly_active": anomaly_active, "messages": []}


def test_two_people_mutes():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 2, "laptop": 1}), {"media_control": media})

    assert media.calls == ["mute"]
    assert result["anomaly_active"] is True
    assert result["narration"]


def test_missing_laptop_mutes():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1}), {"media_control": media})

    assert media.calls == ["mute"]
    assert result["anomaly_active"] is True


def test_latched_and_clear_unmutes():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1, "laptop": 1}, anomaly_active=True), {"media_control": media})

    assert media.calls == ["unmute"]
    assert result["anomaly_active"] is False


def test_cold_and_clear_does_nothing():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1, "laptop": 1}, anomaly_active=False), {"media_control": media})

    assert media.calls == []
    assert result == {}
