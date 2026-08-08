"""The follow-up window: answering without saying the name again.

This is the feature that most easily becomes a runaway loop, and the loop is not
hypothetical — it is what the mechanism is for:

    answer → window opens → the tail of the assistant's own speech is recorded →
    no name needed → a turn runs → a new answer → a new window.

The platform voice plays out of process, so the HUD's `echoCancellation` never
sees it and cannot cancel it. Until this existed, the wake word was the only thing
preventing that, and a follow-up window removes exactly that protection at exactly
the moment audio is playing. So the gate is temporal and it is the mechanism, not
a backstop: an utterance *recorded* while the assistant was talking, or too soon
after it stopped, cannot be a follow-up whatever it says.

The pair of tests that pins it is `test_the_tail_of_its_own_answer_is_not_a_follow_up`
and `test_a_real_reply_a_moment_later_is_a_follow_up`. Same text, same everything,
different recording time — because if the recording time is ever swapped back for
the arrival time, both of those still look reasonable in isolation and only the
pair fails.
"""
import asyncio
import re
from pathlib import Path

import pytest
from core.config import SETTINGS

SETTINGS["server"]["speak"] = False

import server.app as server_app  # noqa: E402

GRACE = server_app.FOLLOW_UP_GRACE_SECONDS
WINDOW = server_app.FOLLOW_UP_SECONDS
# What a segment that recorded the tail of playback actually carries: the
# segmenter does not cut until SILENCE_MS_TO_END of quiet has passed, so its end
# lands that long after the audio stopped.
ECHO_OFFSET = 0.85


class _FakeSpeech:
    """A speech thread that is not talking, and remembers what it said."""

    def __init__(self, quiet_at=0.0, said=(), talking=False):
        self._quiet_at = quiet_at
        self.talking = talking
        self.said = list(said)
        self.silenced = 0

    @property
    def quiet_since(self):
        # None while anything is playing or queued, exactly as the real one does.
        return None if self.talking else self._quiet_at

    def speak(self, text):
        self.said.append(text)

    def silence(self):
        self.silenced += 1

    def sounds_like_something_i_said(self, heard):
        from core.speech_text import resembles

        return any(resembles(heard, said) for said in self.said)


@pytest.fixture(autouse=True)
def _reset():
    server_app._busy = False
    server_app._follow_up_armed_at = None
    server_app._memory_buffer.clear()
    server_app.interrupter.reset()
    yield
    server_app._busy = False
    server_app._follow_up_armed_at = None
    server_app._memory_buffer.clear()


def _arm(at=0.0, speech_quiet_at=0.0):
    server_app._follow_up_armed_at = at
    return _FakeSpeech(quiet_at=speech_quiet_at)


# --------------------------------------------------------------- the gate itself

def test_nothing_is_a_follow_up_before_a_spoken_turn_has_happened(monkeypatch):
    monkeypatch.setattr(server_app, "_speech", _FakeSpeech())
    assert server_app._follow_up_open(1000.0) is False


def test_the_tail_of_its_own_answer_is_not_a_follow_up(monkeypatch):
    """The loop, closed. A segment that was recording while the assistant talked
    is cut SILENCE_MS_TO_END after the audio stops, so it *ends* after playback
    did — which is why the grace interval has to be measured against when the
    recording ended, and has to exceed that silence window."""
    monkeypatch.setattr(server_app, "_speech", _arm(at=100.0, speech_quiet_at=100.0))
    assert server_app._follow_up_open(100.0 + ECHO_OFFSET) is False


def test_a_real_reply_a_moment_later_is_a_follow_up(monkeypatch):
    monkeypatch.setattr(server_app, "_speech", _arm(at=100.0, speech_quiet_at=100.0))
    assert server_app._follow_up_open(103.0) is True


def test_the_window_is_shut_while_it_is_still_talking(monkeypatch):
    speech = _arm(at=100.0)
    speech.talking = True
    monkeypatch.setattr(server_app, "_speech", speech)
    # Even an hour after the turn was armed: if it is talking, nothing being
    # recorded right now is eligible.
    assert server_app._follow_up_open(100.0 + 3600) is False


def test_the_window_closes_on_its_own(monkeypatch):
    monkeypatch.setattr(server_app, "_speech", _arm(at=100.0, speech_quiet_at=100.0))
    assert server_app._follow_up_open(100.0 + WINDOW - 0.1) is True
    assert server_app._follow_up_open(100.0 + WINDOW + 0.1) is False


def test_the_window_runs_from_when_speech_stopped_not_from_when_the_turn_ended(monkeypatch):
    """Speech outlives the turn: the answer is enqueued and the turn returns
    immediately, so the audio is still playing seconds after `_run_prompt` is
    done. Measuring from the turn's end would open the window mid-sentence."""
    monkeypatch.setattr(server_app, "_speech", _arm(at=100.0, speech_quiet_at=106.0))
    # Comfortably inside the window if measured from the turn (100), and before
    # the grace has elapsed if measured from playback ending (106) — which is
    # the correct answer.
    assert server_app._follow_up_open(106.5) is False
    assert server_app._follow_up_open(108.0) is True


def test_an_utterance_with_no_recording_time_can_never_be_a_follow_up(monkeypatch):
    # An older HUD, or any caller with no stamp. The name is then the only way
    # in, which is exactly the behaviour that existed before this window did.
    monkeypatch.setattr(server_app, "_speech", _arm(at=100.0, speech_quiet_at=100.0))
    assert server_app._follow_up_open(None) is False


def test_the_window_works_with_the_voice_switched_off(monkeypatch):
    # server.speak false: nothing is ever spoken, so nothing can be echoed, and
    # the window runs from the end of the turn.
    monkeypatch.setattr(server_app, "_speech", None)
    server_app._follow_up_armed_at = 100.0
    assert server_app._follow_up_open(100.0 + GRACE + 0.1) is True
    assert server_app._follow_up_open(100.0 + GRACE - 0.1) is False


# ------------------------------------------------- the constant it depends on

def test_the_grace_interval_still_exceeds_the_segmenters_silence_window():
    """Cross-language, because nothing else connects these two numbers.

    They live in different languages in different directories. Raising
    SILENCE_MS_TO_END — a plausible, well-motivated change, since a shorter one
    cuts people off at commas — would silently start admitting the tail of every
    answer into the window. That is the runaway loop, arrived at by tuning an
    unrelated-looking constant.
    """
    source = (Path(__file__).resolve().parents[2]
              / "desktop" / "src" / "hooks" / "useAlwaysListening.ts").read_text(encoding="utf-8")
    match = re.search(r"SILENCE_MS_TO_END\s*=\s*([\d_]+)", source)
    assert match, "SILENCE_MS_TO_END is gone from useAlwaysListening.ts; this gate needs rewriting"
    silence_seconds = int(match.group(1).replace("_", "")) / 1000
    assert GRACE > silence_seconds, (
        f"FOLLOW_UP_GRACE_SECONDS ({GRACE}s) must exceed the segmenter's silence window "
        f"({silence_seconds}s), or a segment that recorded the tail of playback ends inside "
        "the follow-up window and the assistant answers itself")


# ------------------------------------------------------- end to end, no socket

class _FakeTranscriber:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text


@pytest.fixture
def heard(monkeypatch):
    """Run one ambient utterance through _handle_utterance, and report what ran."""
    def run(text, captured_at, speech, chat_answer="all done"):
        monkeypatch.setattr(server_app, "_transcriber_task", None)
        monkeypatch.setattr(server_app, "_build_transcriber", lambda: _FakeTranscriber(text))
        monkeypatch.setattr(server_app, "_speech", speech)
        turns = []
        monkeypatch.setattr(
            "core.llm_client.chat",
            lambda *a, **k: turns.append(a) or (
                '{"thought": "", "action": "none", "action_input": {}, '
                f'"final_answer": "{chat_answer}"}}'),
        )
        asyncio.run(server_app._handle_utterance(b"audio", ambient=True, captured_at=captured_at))
        return turns
    return run


def test_a_reply_inside_the_window_runs_without_the_name(heard):
    speech = _arm(at=100.0, speech_quiet_at=100.0)
    assert heard("no, make it Mumbai instead", 103.0, speech), "a reply in the window was discarded"


def test_the_same_reply_outside_the_window_needs_the_name(heard):
    speech = _arm(at=100.0, speech_quiet_at=100.0)
    assert heard("no, make it Mumbai instead", 100.0 + WINDOW + 5, speech) == []


def test_its_own_words_are_refused_even_when_the_timing_would_allow_them(heard):
    """The second line, for the one case the temporal gate cannot cover: the
    operator talks over the answer, so a single segment holds both voices and its
    end lands legitimately inside the window."""
    speech = _arm(at=100.0, speech_quiet_at=100.0)
    speech.said = ["The weather in Bhopal is warm and dry today."]
    assert heard("the weather in bhopal is warm and dry today", 103.0, speech) == []


@pytest.mark.parametrize("reply", [
    # The expensive direction: things the operator says in reply that overlap the
    # words of the answer, because people reply using the words they just heard.
    "make it Mumbai instead",
    "what about tomorrow",
    "is that warmer than yesterday",
    "read that again more slowly",
])
def test_a_real_reply_is_not_mistaken_for_an_echo(heard, reply):
    speech = _arm(at=100.0, speech_quiet_at=100.0)
    speech.said = ["The weather in Bhopal is warm and dry today."]
    assert heard(reply, 103.0, speech), f"{reply!r} was thrown away as an echo"


def test_a_typed_turn_does_not_open_a_voice_window(monkeypatch):
    """Otherwise typing one question means the room can start a turn without the
    name for the next eight seconds — a wider trigger surface, granted to
    somebody who was demonstrably at the keyboard."""
    monkeypatch.setattr(server_app, "_speech", _FakeSpeech())
    monkeypatch.setattr("core.llm_client.chat",
                        lambda *a, **k: '{"thought": "", "action": "none", '
                                        '"action_input": {}, "final_answer": "done"}')
    asyncio.run(server_app._run_and_release("what is the weather"))
    assert server_app._follow_up_armed_at is None

    asyncio.run(server_app._run_and_release("what is the weather", arm_follow_up=True))
    assert server_app._follow_up_armed_at is not None
