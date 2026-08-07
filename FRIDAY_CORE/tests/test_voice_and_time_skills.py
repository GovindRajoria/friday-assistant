"""The two skills the voice work needed, and the volume path it replaced.

`set_volume` is the interesting one. It used to press `volumemute` twice, then
`volumedown` fifty times, then `volumeup` up to fifty more, then play a 1000 Hz
beep — a hundred keystrokes landing on whatever window had focus, to reach a
level that depended on an unverified assumption about the media key's step size.
The tests below describe a skill that sets the level exactly or says it could not,
which is the same standard the mute path was already held to.
"""
import platform
from datetime import date, timedelta

import pytest
from skills.os_control.media_control import MediaControlSkill
from skills.utility.world_time import WorldTimeSkill

IS_WINDOWS = platform.system() == "Windows"


class _FakeKeyboard:
    def __init__(self):
        self.presses = []

    def press(self, key):
        self.presses.append(key)


def _volume_skill(readable=True, settable=True):
    """A skill with the two CoreAudio helpers replaced by an in-memory level."""
    skill = MediaControlSkill()
    state = {"level": 40}

    def read():
        return state["level"] if readable else None

    def write(percent):
        if not settable:
            return None
        state["level"] = percent
        return percent

    skill._volume_percent = read
    skill._set_volume_percent = write
    skill._state = state
    return skill


# ------------------------------------------------------------------- set_volume

@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
@pytest.mark.parametrize(("asked", "expected"), [(30, 30), (0, 0), (100, 100), ("70", 70), (150, 100), (-5, 0)])
def test_a_level_is_set_exactly_and_clamped(asked, expected):
    skill = _volume_skill()
    result = skill.execute({"action": "set_volume", "level": asked})

    assert result["status"] == "success"
    assert skill._state["level"] == expected
    assert f"{expected}%" in result["message"]


@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
def test_setting_a_level_presses_no_keys_at_all():
    """The whole point. A hundred keypresses take a visible moment and land on
    whichever window has focus."""
    skill = _volume_skill()
    keyboard = _FakeKeyboard()
    import skills.os_control.media_control as module

    original = module.__dict__.get("pyautogui")
    try:
        skill.execute({"action": "set_volume", "level": 30})
    finally:
        if original is not None:
            module.pyautogui = original
    # Nothing was pressed because nothing needed to be: the level is set through
    # the audio interface, not simulated by stepping.
    assert keyboard.presses == []


@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
def test_an_unreachable_audio_interface_is_reported_not_approximated():
    # Same standard the mute path is held to: a state that cannot be set is a
    # failure to say out loud, not a thing to guess at with the media keys.
    skill = _volume_skill(readable=False, settable=False)
    result = skill.execute({"action": "set_volume", "level": 30})

    assert result["status"] == "error"
    assert "specific level" in result["message"]


@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
def test_a_level_that_is_not_a_number_is_refused():
    skill = _volume_skill()
    result = skill.execute({"action": "set_volume", "level": "loud"})

    assert result["status"] == "error"
    assert skill._state["level"] == 40, "the level moved despite the request being nonsense"


# -------------------------------------------------------------- relative moves

@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
@pytest.mark.parametrize(("action", "expected"), [
    ("volume_up", 50), ("volume_down", 30),
    # The model paraphrases, and an unknown action reads to the operator as the
    # skill being broken rather than as a vocabulary mismatch.
    ("louder", 50), ("quieter", 30), ("turn down", 30), ("Louder", 50),
])
def test_a_relative_move_lands_where_it_should(action, expected):
    skill = _volume_skill()
    result = skill.execute({"action": action})

    assert result["status"] == "success"
    assert skill._state["level"] == expected


@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
def test_a_relative_move_falls_back_to_the_media_keys(monkeypatch):
    """The one case a blind keypress is honest: there is no target state to get
    wrong, only a direction — which is exactly why mute cannot do this."""
    skill = _volume_skill(readable=False, settable=False)
    keyboard = _FakeKeyboard()
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", keyboard)

    result = skill.execute({"action": "volume_down", "level": 10})

    assert result["status"] == "success"
    assert keyboard.presses == ["volumedown"] * 5
    assert "unreadable" in result["message"]


@pytest.mark.skipif(not IS_WINDOWS, reason="the audio path is Windows-only by design")
def test_the_volume_can_be_read_back():
    assert "40%" in _volume_skill().execute({"action": "get_volume"})["message"]


# ----------------------------------------------------------------- world_time

def test_the_local_time_is_answered_without_a_place():
    result = WorldTimeSkill().execute({})
    assert result["status"] == "success"
    assert date.today().strftime("%A") in result["message"]


@pytest.mark.parametrize(("place", "expect"), [
    ("Tokyo", "Asia/Tokyo"),
    ("tokyo", "Asia/Tokyo"),
    ("london", "Europe/London"),
    ("new york", "America/New_York"),
    ("Asia/Kolkata", "Asia/Kolkata"),
    # The handful whose IANA name nobody would guess.
    ("UK", "Europe/London"),
    ("india", "Asia/Kolkata"),
    ("utc", "UTC"),
])
def test_a_place_is_resolved_from_the_real_timezone_database(place, expect):
    """Matched against zoneinfo rather than a hand-kept table of cities, which is
    why this works for places nobody thought to list."""
    result = WorldTimeSkill().execute({"action": "time_in", "place": place})
    assert result["status"] == "success", result["message"]
    assert expect in result["message"]


def test_an_unknown_place_says_so_rather_than_answering_about_here():
    """The failure that matters: answering "what time is it in Narnia" with the
    local time is a confident wrong answer, not an error."""
    result = WorldTimeSkill().execute({"action": "time_in", "place": "Narnia"})
    assert result["status"] == "error"
    assert "Narnia" in result["message"]


def test_an_offset_is_reported_in_hours_and_minutes_not_as_a_decimal():
    """A sixth of the world is not on a whole-hour offset, including where this
    assistant runs. As a decimal rounded to one place, the 45-minute gap between
    Kolkata and Kathmandu came out as "0.2 hours ahead" — unnatural to hear and
    also wrong."""
    result = WorldTimeSkill().execute({"action": "time_in", "place": "Asia/Kathmandu"})
    assert result["status"] == "success"
    assert "." not in result["message"].split(" in ")[1].rstrip("."), result["message"]
    assert "minute" in result["message"] or "same time" in result["message"]


def test_a_date_read_aloud_has_no_leading_zero_in_it():
    # "Friday oh-seven August" is not a date anybody says, and this answer is
    # spoken.
    message = WorldTimeSkill().execute({})["message"]
    assert f" {date.today().day} " in message, message


@pytest.mark.parametrize(("asked", "days"), [(0, 0), (1, 1), (30, 30), (-1, -1)])
def test_days_until_a_date_is_counted_from_today(asked, days):
    target = date.today() + timedelta(days=asked)
    result = WorldTimeSkill().execute({"action": "until", "date": target.isoformat()})

    assert result["status"] == "success"
    if days == 0:
        assert "today" in result["message"]
    elif days == 1:
        assert "tomorrow" in result["message"]
    elif days == -1:
        assert "yesterday" in result["message"]
    else:
        assert f"{days} days until" in result["message"]


def test_a_named_date_always_means_the_next_one():
    """Asked on Boxing Day, "how long until Christmas" means next Christmas —
    not minus one day, which is what a naive this-year lookup returns."""
    result = WorldTimeSkill().execute({"action": "until", "date": "christmas"})
    assert result["status"] == "success"
    assert "December" in result["message"]
    assert "ago" not in result["message"]


def test_a_date_it_cannot_read_is_reported():
    result = WorldTimeSkill().execute({"action": "until", "date": "some time next quarter"})
    assert result["status"] == "error"
    assert "2026-12-25" in result["message"], "the error should show a form that works"


def test_a_date_with_no_action_is_still_treated_as_a_countdown():
    # The model fills the parameter and omits the action about as often as the
    # reverse, and answering a question about a date with the local time is a
    # wrong answer rather than a failure.
    result = WorldTimeSkill().execute({"date": (date.today() + timedelta(days=5)).isoformat()})
    assert "5 days until" in result["message"]


# --------------------------------------------------------------- voice_control

@pytest.fixture
def voice(monkeypatch):
    """The skill with its writer stubbed, so nothing touches settings.yaml."""
    from skills.utility.voice_control import VoiceControlSkill

    skill = VoiceControlSkill()
    written = {}
    monkeypatch.setattr(skill, "_write",
                        lambda key, value: written.update({key: value}) or {"status": "success"})
    skill.written = written
    return skill


def test_slower_and_faster_move_by_a_step_that_can_be_heard(voice, monkeypatch):
    from core.config import SETTINGS

    monkeypatch.setitem(SETTINGS["audio"], "speech_rate", 175)
    assert voice.execute({"action": "slower"})["status"] == "success"
    assert voice.written["audio.speech_rate"] == 150

    assert voice.execute({"action": "faster"})["status"] == "success"
    assert voice.written["audio.speech_rate"] == 200


def test_the_rate_is_clamped_to_something_intelligible(voice):
    voice.execute({"action": "set_rate", "value": 9000})
    assert voice.written["audio.speech_rate"] == 320
    voice.execute({"action": "set_rate", "value": 1})
    assert voice.written["audio.speech_rate"] == 90


def test_a_rate_that_is_not_a_number_is_refused(voice):
    result = voice.execute({"action": "set_rate", "value": "quickly"})
    assert result["status"] == "error"
    assert voice.written == {}


def test_it_confirms_by_demonstrating_rather_than_claiming(voice, monkeypatch):
    """Spoken at the new rate, because core/speaker.py re-reads the setting before
    every utterance — so the answer to "is that better?" is the answer itself."""
    from core.config import SETTINGS

    monkeypatch.setitem(SETTINGS["audio"], "speech_rate", 175)
    message = voice.execute({"action": "slower"})["message"]
    assert "150 words a minute" in message
    assert "better" in message


def test_being_asked_to_stop_speaking_says_when_that_takes_effect(voice):
    # server.speak is read at process start, so this one genuinely cannot apply
    # until then. Saying so is the difference between a limitation and a lie.
    result = voice.execute({"action": "be_quiet"})
    assert voice.written["server.speak"] is False
    assert "restart" in result["message"]
    assert "say stop" in result["message"]


def test_changing_voice_admits_it_cannot_rather_than_claiming_it_did(voice, monkeypatch):
    """The failure this project treats as the worst available: reporting an action
    that did not happen. Which voice is used is fixed in core/speaker.py with no
    setting behind it, so there is nothing to write."""
    monkeypatch.setattr(voice, "_voices", lambda: [(0, "David"), (1, "Zira")])
    result = voice.execute({"action": "set_voice", "value": "David"})

    assert result["status"] == "error"
    assert "cannot change my voice" in result["message"]
    assert "Zira" in result["message"], "it should still say what is installed"
    assert voice.written == {}


def test_an_unknown_request_lists_what_it_can_actually_do(voice):
    result = voice.execute({"action": "sing"})
    assert result["status"] == "error"
    assert "faster or slower" in result["message"]


def test_the_rate_can_be_reported_without_changing_it(voice, monkeypatch):
    from core.config import SETTINGS

    monkeypatch.setitem(SETTINGS["audio"], "speech_rate", 190)
    result = voice.execute({"action": "get_rate"})
    assert "190 words a minute" in result["message"]
    assert voice.written == {}
