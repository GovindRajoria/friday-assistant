"""core/speech_text.py — what gets said out loud, and where it is cut.

Two properties matter more than any individual rewrite here:

  * **No word is lost.** Markers come off; the text between them stays. A voice
    filter that quietly drops content is worse than one that says "star", because
    the operator has no way to notice it happened.
  * **A sentence is never cut inside a number.** "Version 3. 5." and "one point
    two gigabytes" split into nonsense fragments read aloud, and the model writes
    figures constantly.
"""
import pytest
from core.speech_text import for_speech, sentences


@pytest.mark.parametrize(("written", "spoken"), [
    ("**Ready.**", "Ready."),
    ("*ready*", "ready"),
    ("***very* ready**", "very ready"),
    ("_ready_ now", "ready now"),
    ("The file is `config.yaml`", "The file is config.yaml"),
    # The asterisks inside a glob would otherwise pair up as emphasis and eat
    # the path between them.
    ("Checked `src/**/*.ts` for it", "Checked src/**/*.ts for it"),
    ("## Summary\nAll good", "Summary\nAll good"),
    ("- one\n- two", "one\ntwo"),
    ("* one\n+ two", "one\ntwo"),
    ("1. one\n2) two", "one\ntwo"),
    ("> quoted line", "quoted line"),
    ("| a | b |\n| 1 | 2 |", "a b\n1 2"),
    ("above\n\n---\n\nbelow", "above\nbelow"),
])
def test_markers_come_off_and_the_words_stay(written, spoken):
    assert for_speech(written) == spoken


@pytest.mark.parametrize("written", [
    "See https://example.com/a/b?c=d for details",
    "See www.example.com/a/b for details",
])
def test_a_url_becomes_a_phrase_rather_than_forty_seconds_of_punctuation(written):
    assert for_speech(written) == "See a link for details"


def test_a_markdown_link_keeps_its_words_and_loses_its_target():
    assert for_speech("Read [the docs](https://example.com/docs) first") == "Read the docs first"


def test_a_code_block_is_described_rather_than_read_out():
    answer = "Run this:\n```bash\ncd repo\nnpm install\nnpm test\n```\nThen check it."
    spoken = for_speech(answer)
    assert "npm install" not in spoken
    assert "a 3-line code block, on screen." in spoken
    assert spoken.startswith("Run this:")
    assert spoken.endswith("Then check it.")


def test_a_one_line_code_block_is_just_said():
    # Short enough to be the answer itself — "a 1-line code block, on screen"
    # would be withholding the only thing the operator asked for.
    assert for_speech("```\ngit status\n```") == "git status."


def test_an_unterminated_fence_does_not_swallow_the_rest_of_the_answer():
    # The model truncates. Without the \Z alternative in the pattern, an answer
    # cut off mid-block would leave the fence markers in and read them aloud.
    spoken = for_speech("Here:\n```python\nx = 1")
    assert "```" not in spoken
    assert "x = 1" in spoken


def test_nothing_is_left_that_a_voice_would_pronounce_as_punctuation():
    answer = ("**Done.** I checked `main.py`, see [the log](http://localhost:8756/log)\n"
              "- it built\n- 2 tests failed\n\n```\npytest -q\n```")
    spoken = for_speech(answer)
    for marker in ("**", "`", "](", "http", "\n- "):
        assert marker not in spoken, f"{marker!r} survived into speech"


def test_empty_input_stays_empty():
    assert for_speech("") == ""
    assert for_speech("   \n  ") == ""
    assert sentences("") == []


@pytest.mark.parametrize(("text", "expected"), [
    ("One. Two. Three.", ["One.", "Two.", "Three."]),
    ("Is it? Yes! Good.", ["Is it?", "Yes!", "Good."]),
    ('He said "no". Then left.', ['He said "no".', "Then left."]),
    # Each list item is its own unit even with no punctuation at all, which is
    # how the model actually writes them.
    ("one\ntwo\nthree", ["one", "two", "three"]),
])
def test_speech_is_cut_into_units_that_can_be_interrupted_between(text, expected):
    assert sentences(text) == expected


@pytest.mark.parametrize("text", [
    "Version 3.5 is installed.",
    "It used 1.2 GB of memory.",
    "The file is 0.75 seconds long.",
])
def test_a_decimal_is_never_a_sentence_boundary(text):
    assert sentences(text) == [text]


@pytest.mark.parametrize(("text", "count"), [
    ("Dr. Smith called. He is waiting.", 2),
    ("Use pytest, mypy, etc. Then commit.", 2),
    ("J. R. Tolkien wrote it.", 1),
    ("It is 4 p.m. Time to stop.", 2),
])
def test_an_abbreviation_is_not_the_end_of_a_sentence(text, count):
    assert len(sentences(text)) == count


def test_a_sentence_split_loses_no_words():
    """The property that matters most, asserted as a property.

    Any bug in the boundary logic that drops a fragment would be inaudible in a
    review of the code and obvious only to whoever was listening.
    """
    answer = ("Ready. I found 3 things: the disk is 82% full, version 1.2 is "
              "installed, and Dr. Adams replied.\nNothing else needs doing!")
    spoken = for_speech(answer)
    assert " ".join(sentences(spoken)).split() == spoken.split()


def test_the_two_halves_compose_the_way_the_speech_thread_uses_them():
    # for_speech first, then sentences — the order server/app.py enqueues in.
    # Reversed, a fence spanning lines would be split before it is recognised.
    answer = "**Two things.**\n```\na\nb\n```\nThat is all."
    pieces = sentences(for_speech(answer))
    assert pieces == ["Two things.", "a 2-line code block, on screen.", "That is all."]
