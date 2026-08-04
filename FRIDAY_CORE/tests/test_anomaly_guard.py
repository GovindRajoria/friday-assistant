# tests/test_anomaly_guard.py
"""Table-driven coverage of the anomaly rule. No model, camera or microphone involved.

Two axes now: whether the rule fires (detections) and what it is allowed to do
about it (privacy settings). The muting cases have to name `auto_mute: True`
explicitly — the shipped default does not mute, and a test that passed silently
under either default would not be testing the switch.
"""
import yaml
from core.config import CONFIG_DIR, DEFAULTS
from core.nodes.anomaly_guard import anomaly_guard_node

MUTING = {"privacy": {"auto_mute": True, "announce_only": True}}
ANNOUNCE_ONLY = {"privacy": {"auto_mute": False, "announce_only": True}}
SILENT = {"privacy": {"auto_mute": False, "announce_only": False}}


class _FakeMediaControl:
    def __init__(self, status="success"):
        self.calls = []
        self.status = status

    def execute(self, params=None):
        action = (params or {}).get("action")
        self.calls.append(action)
        return {"status": self.status, "message": f"{action} applied"}


def _state(detections, anomaly_active=False):
    return {"detections": detections, "anomaly_active": anomaly_active, "messages": []}


def test_two_people_mutes_when_opted_in():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 2, "laptop": 1}), {"media_control": media}, MUTING)

    assert media.calls == ["mute"]
    assert result["anomaly_active"] is True
    assert "Audio muted" in result["narration"][0]


def test_missing_laptop_mutes_when_opted_in():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1}), {"media_control": media}, MUTING)

    assert media.calls == ["mute"]
    assert result["anomaly_active"] is True


def test_latched_and_clear_unmutes_when_opted_in():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1, "laptop": 1}, anomaly_active=True),
                                {"media_control": media}, MUTING)

    assert media.calls == ["unmute"]
    assert result["anomaly_active"] is False
    assert "Audio restored" in result["narration"][0]


def test_cold_and_clear_does_nothing():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1, "laptop": 1}, anomaly_active=False),
                                {"media_control": media}, MUTING)

    assert media.calls == []
    assert result == {}


def test_default_announces_without_muting():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 2, "laptop": 1}), {"media_control": media},
                                ANNOUNCE_ONLY)

    assert media.calls == []
    assert result["anomaly_active"] is True
    assert result["narration"]
    assert "mute" not in result["narration"][0].lower()


def test_default_clear_announces_without_unmuting():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 1, "laptop": 1}, anomaly_active=True),
                                {"media_control": media}, ANNOUNCE_ONLY)

    assert media.calls == []
    assert result["anomaly_active"] is False
    assert "Audio restored" not in result["narration"][0]


def test_both_off_is_a_no_op():
    media = _FakeMediaControl()
    result = anomaly_guard_node(_state({"person": 2, "laptop": 1}), {"media_control": media}, SILENT)

    assert media.calls == []
    assert result == {}


def test_built_in_default_does_not_mute():
    """A checkout with no settings file at all must still not mute."""
    assert DEFAULTS["privacy"]["auto_mute"] is False
    assert DEFAULTS["privacy"]["announce_only"] is True


def test_shipped_example_does_not_mute():
    """The tracked example file, read directly rather than through the loader.

    Separate from the test above on purpose, and deliberately not asserting on
    load_settings(): settings.example.yaml is tracked and overrides DEFAULTS, so
    the built-in value being right is not evidence that the shipped one is —
    while config/settings.yaml is the operator's own and is entitled to turn
    auto_mute on. Only the two artifacts in version control are gated here.
    """
    example = yaml.safe_load((CONFIG_DIR / "settings.example.yaml").read_text(encoding="utf-8"))

    assert example["privacy"]["auto_mute"] is False
    assert example["privacy"]["announce_only"] is True


def test_narration_does_not_claim_a_mute_that_failed():
    media = _FakeMediaControl(status="error")
    result = anomaly_guard_node(_state({"person": 2, "laptop": 1}), {"media_control": media}, MUTING)

    assert media.calls == ["mute"]
    assert "Audio muted" not in result["narration"][0]
    assert "did not mute" in result["narration"][0]


def test_narration_does_not_claim_a_mute_without_the_skill():
    result = anomaly_guard_node(_state({"person": 2, "laptop": 1}), {}, MUTING)

    assert "Audio muted" not in result["narration"][0]
    assert "media_control is not loaded" in result["narration"][0]
    assert result["anomaly_active"] is True
