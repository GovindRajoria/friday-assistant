"""_SpeechThread: the queue, the interruption, and the clock other code trusts.

No engine and no audio device here — a fake speaker is injected, which is the
only reason any of this is testable. What is being checked is not that sound
comes out; it is the three properties other code depends on:

  * an answer is queued as sentences, so `silence()` can stop it partway;
  * `speaking` covers what is queued as well as what is playing;
  * `quiet_since` marks when playback *finished*, and does not tick over between
    the sentences of one answer.

The last one is load-bearing rather than cosmetic. The follow-up window that lets
somebody speak without repeating the wake word is opened off this clock, and the
platform voice plays out of process where the microphone's echo cancellation
cannot reach it. A clock that reported "quiet since a moment ago" in the middle of
an answer would open that window while the speakers were still going, and the
assistant would hear itself and reply.
"""
import threading
import time

import pytest
from core.config import SETTINGS

SETTINGS["server"]["speak"] = False  # importing must not spin up a real engine

import server.app as server_app  # noqa: E402


class _FakeSpeaker:
    """Records what it was asked to say. Optionally blocks inside a call.

    Blocking is how "is it talking right now" becomes observable at all: without
    it, every utterance finishes before the test can look.
    """

    def __init__(self, block=False):
        self.said = []
        self._block = block
        self.speaking_now = threading.Event()
        self.release = threading.Event()

    def speak(self, text):
        self.said.append(text)
        self.speaking_now.set()
        if self._block:
            # Bounded, so a test that forgets to release cannot hang the suite.
            self.release.wait(timeout=10)


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def speech():
    """A running speech thread and its fake speaker, torn down afterwards."""
    threads = []

    def build(block=False):
        speaker = _FakeSpeaker(block=block)
        thread = server_app._SpeechThread(SETTINGS, speaker_factory=lambda: speaker)
        threads.append((thread, speaker))
        return thread, speaker

    yield build
    for thread, speaker in threads:
        speaker.release.set()
        thread.stop()


def test_an_answer_is_queued_as_the_sentences_it_will_be_said_in(speech):
    thread, speaker = speech()
    thread.speak("Ready. I found two things. Nothing else needs doing.")

    assert _wait_until(lambda: len(speaker.said) == 3), speaker.said
    assert speaker.said == ["Ready.", "I found two things.", "Nothing else needs doing."]


def test_markdown_never_reaches_the_engine(speech):
    """The most unprofessional thing this assistant did: reading "star star"."""
    thread, speaker = speech()
    thread.speak("**Done.** I checked `main.py`, see https://example.com/log")

    assert _wait_until(lambda: len(speaker.said) == 2), speaker.said
    assert speaker.said == ["Done.", "I checked main.py, see a link"]


def test_silence_drops_what_is_still_queued(speech):
    thread, speaker = speech(block=True)
    thread.speak("One. Two. Three. Four. Five.")
    assert _wait_until(speaker.speaking_now.is_set)

    thread.silence()
    speaker.release.set()

    # The sentence already in the engine cannot be recalled — there is no safe
    # way to interrupt it from another thread, which is the whole reason the
    # queue is per sentence. Everything behind it is gone.
    assert _wait_until(lambda: not thread.speaking)
    assert speaker.said == ["One."]


def test_speaking_covers_what_is_queued_as_well_as_what_is_playing(speech):
    thread, speaker = speech(block=True)
    thread.speak("One. Two. Three.")
    assert _wait_until(speaker.speaking_now.is_set)
    # Mid-answer: one sentence in the engine, two waiting. A caller asking this
    # to decide whether to listen wants "will sound come out in a moment", and
    # the answer is yes.
    assert thread.speaking is True
    assert thread.quiet_since is None

    speaker.release.set()
    assert _wait_until(lambda: not thread.speaking)
    assert thread.quiet_since is not None


def test_the_quiet_clock_does_not_tick_over_between_sentences(speech):
    """The trap this clock exists to avoid, asserted directly.

    Setting it after every sentence would report the assistant as quiet four
    times during a five-sentence answer, and each of those is a moment the
    follow-up window would have opened over the top of its own voice.
    """
    thread, speaker = speech()
    thread.speak("One. Two. Three. Four. Five.")
    assert _wait_until(lambda: len(speaker.said) == 5), speaker.said
    assert _wait_until(lambda: thread.quiet_since is not None)
    settled = thread.quiet_since

    # Nothing further queued, so it must stay put rather than drifting forward.
    time.sleep(0.05)
    assert thread.quiet_since == settled


def test_it_starts_out_quiet_rather_than_claiming_to_talk(speech):
    thread, _ = speech()
    assert thread.speaking is False
    assert thread.quiet_since is not None


def test_an_empty_or_markup_only_answer_queues_nothing(speech):
    thread, speaker = speech()
    thread.speak("")
    thread.speak("   ")
    thread.speak("---")
    time.sleep(0.1)
    assert speaker.said == []
    assert thread.speaking is False
