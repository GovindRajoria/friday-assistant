# tests/test_wake_word.py
"""The wake-word gate. Both directions of failure matter, and not equally.

A missed wake word costs one repetition. A false trigger runs a turn on a
conversation nobody addressed to the assistant — and with continuous listening
that means a sentence spoken to another person in the room. So the tests below
are heavier on the false-positive side, and the mishearing cases are written from
what `small.en` actually does with a name: it splits it, or renders it as a
different word entirely.
"""
import pytest
from core.wake_word import find


@pytest.mark.parametrize("heard, expected_command", [
    ("Friday, what's the weather?", "what's the weather"),
    ("friday what is the weather", "what is the weather"),
    ("Friday. Read me that document.", "read me that document"),
    ("hey friday, lock the screen", "lock the screen"),
    ("ok friday what time is it", "what time is it"),
    ("Hello Friday, how are you?", "how are you"),
    ("please friday translate this", "translate this"),
])
def test_a_sentence_addressed_to_the_assistant_is_taken(heard, expected_command):
    addressed, command = find(heard)

    assert addressed is True
    assert command == expected_command


@pytest.mark.parametrize("heard", [
    "Fry day, what's the weather",
    "Fri day what is the weather",
    "Freeday, read the news",
    "friyay lock the screen",
    "frydey what time is it",
    "free day what's the news",
])
def test_a_mishearing_of_the_name_still_counts(heard):
    """small.en is an English-only model being asked for an Indian English
    speaker's pronunciation of a proper noun. A substring test misses all of
    these and the assistant appears deaf."""
    addressed, command = find(heard)

    assert addressed is True, f"{heard!r} should have been recognised as address"
    assert command, "the command after the name must survive"


@pytest.mark.parametrize("heard", [
    "I'll send it to you on Friday",
    "the meeting is Friday afternoon",
    "let's do it Friday",
    "I was thinking maybe Friday or Saturday",
    "so the deadline moved to Friday",
])
def test_the_name_used_inside_a_sentence_is_not_an_address(heard):
    """The failure that matters: with auto-submit, this becomes a turn nobody
    asked for, from a conversation with another person."""
    addressed, _command = find(heard)

    assert addressed is False, f"{heard!r} is a sentence about a day, not an address"


@pytest.mark.parametrize("heard", [
    "what's the weather",
    "delete the notes",
    "",
    "   ",
    "priority one",
    "happy birthday",
    "the fridge is empty",
])
def test_an_utterance_without_the_name_is_ignored(heard):
    addressed, command = find(heard)

    assert addressed is False
    assert command == ""


def test_the_name_alone_is_an_address_with_no_command():
    """Handled by the caller as "yes?" — they want attention, not nothing."""
    addressed, command = find("Friday")

    assert addressed is True
    assert command == ""


def test_the_name_alone_with_punctuation():
    addressed, command = find("Friday?")

    assert addressed is True
    assert command == ""


def test_a_configured_wake_word_is_honoured():
    addressed, command = find("Jarvis, what's the weather", wake_word="jarvis")

    assert addressed is True
    assert command == "what's the weather"


def test_the_default_name_is_not_matched_when_another_is_configured():
    addressed, _ = find("Friday, what's the weather", wake_word="computer")

    # "friday" is in the known-variants set, which is specific to the default
    # name — a different configured word must not inherit those.
    assert addressed is False


def test_case_and_punctuation_do_not_matter():
    for heard in ("FRIDAY, WHAT TIME IS IT", "friday... what time is it",
                  "Friday -- what time is it"):
        addressed, command = find(heard)
        assert addressed is True
        assert "what time is it" in command


def test_a_long_preamble_before_the_name_is_not_an_address():
    """Three leading words is the bound; past that the name is being used, not
    spoken to."""
    addressed, _ = find("I was just about to say Friday would work")

    assert addressed is False


@pytest.mark.parametrize("heard", [
    "Thursday, are we still on?",
    "Thursday works for me",
    "see you Thursday",
])
def test_thursday_is_never_treated_as_the_wake_word(heard):
    """A real, different, commonly-spoken word. Accepting it as a mishearing
    would mean a sentence to another person in the room starts a turn."""
    addressed, _ = find(heard)

    assert addressed is False
