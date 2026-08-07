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
from core.wake_word import find, is_stop_command


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


@pytest.mark.parametrize("heard", [
    "stop",
    "Stop.",
    "friday stop",
    "hey friday, stop",
    "stop talking",
    "be quiet",
    "quiet please",
    "shut up",
    "never mind",
    "that is enough",
    "cancel that",
])
def test_asking_it_to_be_quiet_is_recognised_without_the_name(heard):
    """Requiring the name from somebody interrupting would be the worst possible
    moment to insist on protocol — they are already talking over an answer."""
    assert is_stop_command(heard) is True


@pytest.mark.parametrize("heard", [
    # The expensive half. Every one of these is a real request, and three of them
    # reach a destructive skill — swallowing them as "be quiet" would look like
    # the assistant ignoring an instruction it had actually heard perfectly.
    "stop the build",
    "stop the container",
    "cancel the deployment",
    "cancel my meeting at four",
    "stop the music",
    "quiet the notifications",
    "never mind the weather, read the news",
    "forget it and open notepad",
    # Its own speech, arriving back through an open microphone. This is the case
    # the whole-utterance match exists for.
    "I have stopped the service.",
    "Stopped the build, as you asked.",
    "That is enough disk space for now.",
])
def test_a_real_request_is_never_swallowed_as_a_command_to_be_quiet(heard):
    assert is_stop_command(heard) is False


def test_the_name_alone_is_not_a_command_to_be_quiet():
    # It is a request for attention, answered with "yes?" — and after the name is
    # stripped nothing is left, which must not fall through to a match on "".
    assert is_stop_command("friday") is False
    assert is_stop_command("") is False
    assert is_stop_command("   ") is False


def test_a_configured_name_is_stripped_and_the_default_is_not():
    assert is_stop_command("computer stop", wake_word="computer") is True
    # "friday stop" under a renamed assistant is two words that are not a stop
    # phrase, exactly as `find` refuses to inherit the default's variants.
    assert is_stop_command("friday stop", wake_word="computer") is False
