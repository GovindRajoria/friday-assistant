# tests/test_media_control_mute.py
"""The mute path, with the COM layer stubbed out.

What is being pinned down here is the one thing that actually went wrong on
hardware: the media key *toggles*, so "mute" implemented as a blind keypress
unmutes an already-muted machine. These tests describe a skill that reaches a
known state or admits it could not, and never reports success for a state it
cannot read.
"""
import pytest
from skills.os_control.media_control import MediaControlSkill


class _FakeKeyboard:
    def __init__(self):
        self.presses = []

    def press(self, key):
        self.presses.append(key)


def _skill(coreaudio_set, mute_states):
    """A skill with the two COM helpers replaced.

    `mute_states` is consumed one reading at a time, so a test can describe the
    state before and after a keypress.
    """
    skill = MediaControlSkill()
    readings = list(mute_states)
    skill._set_mute_via_coreaudio = lambda muted: coreaudio_set
    skill._is_muted_via_coreaudio = lambda: readings.pop(0) if readings else None
    return skill


@pytest.mark.parametrize("muted, wanted", [(True, "muted"), (False, "unmuted")])
def test_coreaudio_path_is_preferred(muted, wanted):
    skill = _skill(coreaudio_set=True, mute_states=[])
    keyboard = _FakeKeyboard()

    result = skill._apply_mute(muted, keyboard)

    assert result["status"] == "success"
    assert wanted in result["message"]
    assert keyboard.presses == []          # no toggle needed when the state can be set


def test_unreadable_state_is_an_error_not_a_blind_keypress():
    skill = _skill(coreaudio_set=False, mute_states=[None])
    keyboard = _FakeKeyboard()

    result = skill._apply_mute(True, keyboard)

    assert result["status"] == "error"
    assert keyboard.presses == []
    assert "only toggles" in result["message"]


def test_already_in_the_wanted_state_presses_nothing():
    skill = _skill(coreaudio_set=False, mute_states=[True])
    keyboard = _FakeKeyboard()

    result = skill._apply_mute(True, keyboard)

    assert result["status"] == "success"
    assert "already muted" in result["message"]
    assert keyboard.presses == []


def test_wrong_state_toggles_once_and_confirms():
    skill = _skill(coreaudio_set=False, mute_states=[False, True])
    keyboard = _FakeKeyboard()

    result = skill._apply_mute(True, keyboard)

    assert result["status"] == "success"
    assert keyboard.presses == ["volumemute"]


def test_toggle_that_did_not_take_is_reported_as_failure():
    skill = _skill(coreaudio_set=False, mute_states=[False, False])
    keyboard = _FakeKeyboard()

    result = skill._apply_mute(True, keyboard)

    assert result["status"] == "error"
    assert keyboard.presses == ["volumemute"]
    assert "did not leave" in result["message"]
