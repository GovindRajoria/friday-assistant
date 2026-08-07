"""core/nearest.py — and the reason it suggests rather than corrects.

The single most important property here is a negative one: nothing in this module
may be used to silently replace what somebody said. The docstring records the
measurement that forces that, and `test_the_fantasies_score_higher_than_the_
mishearings` re-derives it from the live timezone database, so if a future reader
decides the caution was excessive the data says otherwise in one test run.
"""
import pytest
from core.nearest import SUGGEST_RATIO, did_you_mean, nearest

APPS = ["chrome", "notepad", "calculator", "spotify", "task manager", "control panel"]


@pytest.mark.parametrize(("misheard", "expected"), [
    ("notepadd", "notepad"),
    ("note pad", "notepad"),
    ("crome", "chrome"),
    ("chroem", "chrome"),
    ("calculater", "calculator"),
    ("spotifi", "spotify"),
    ("taskmanager", "task manager"),
])
def test_a_mishearing_finds_the_thing_that_was_meant(misheard, expected):
    assert nearest(misheard, APPS) == expected


@pytest.mark.parametrize("nonsense", ["", "   ", "xyzzy", "qqqqqqqq"])
def test_nothing_close_returns_nothing(nonsense):
    assert nearest(nonsense, APPS) is None


def test_the_option_comes_back_spelled_the_way_it_was_given():
    # Case-insensitive comparison, but the caller shows the canonical spelling.
    assert nearest("TASK MANAGER", APPS) == "task manager"
    assert nearest("Chrome", ["Google Chrome", "Firefox"]) == "Google Chrome"


def test_the_phrase_is_empty_when_there_is_nothing_to_suggest():
    """Returns "" rather than None so a caller can concatenate it unconditionally
    — a suggestion that is sometimes None is a branch at every call site, and a
    forgotten branch prints "None" at the operator."""
    assert did_you_mean("qqqqqqqq", APPS) == ""
    assert did_you_mean("notepadd", APPS) == " Did you mean notepad?"


def test_an_empty_option_list_is_not_an_error():
    assert nearest("chrome", []) is None
    assert did_you_mean("chrome", []) == ""


def test_the_fantasies_score_higher_than_the_mishearings():
    """The measurement that makes suggesting the only safe option.

    If a future change replaces `did_you_mean` with a silent correction, this is
    the test that should have stopped it: the real mishearings this is for score
    *below* the imaginary places it must never answer about, so no threshold
    separates them. Asserted against the live timezone database rather than a
    frozen table, so it stays true as that database changes.
    """
    from zoneinfo import available_timezones

    pool = {name.lower().rsplit("/", 1)[-1] for name in available_timezones()}

    def score(word):
        import difflib
        return max(difflib.SequenceMatcher(None, word, option).ratio() for option in pool)

    misheard = score("dukyo")      # meant Tokyo
    fantasy = score("atlantis")    # meant nowhere at all

    assert fantasy > misheard, (
        "the measurement this module is built on no longer holds: if a real "
        "mishearing now outscores a fantasy, a substitution threshold may be safe")
    assert misheard >= SUGGEST_RATIO, (
        f"'dukyo' scores {misheard:.2f}, below the suggest threshold "
        f"{SUGGEST_RATIO} — the case this module exists for would get no suggestion")
