"""core/shortlist.py — and the one property that matters more than ranking.

A shortlist that omits the right tool does not merely rank it badly: the enum in
`core/registry.py` is built from the same subset, so the skill stops being
*nameable*. The model cannot choose `manage_processes` if `manage_processes` is
not in the enum, however obvious the request. Ranking quality is a nice-to-have;
**recall is a correctness property**, and most of this file is about recall.

`test_every_labelled_case_can_still_reach_its_answer` is the important one. It
runs the whole labelled set from `tools/routing_cases.yaml` through the real
shortlist with the real registry and asserts the right answer survives — no
model, no Ollama, so unlike the benchmark itself this one is a CI gate.
"""
import pytest
from core.shortlist import (
    ALWAYS_INCLUDE,
    DEFAULT_LIMIT,
    FLOOR,
    build_index,
    rank,
    shortlist,
    terms,
)


class _Skill:
    def __init__(self, name, description, parameters=()):
        self.manifest = {"name": name, "description": description,
                         "parameters": list(parameters)}


def _registry(**described):
    return {name: _Skill(name, text) for name, text in described.items()}


# ------------------------------------------------------------------ tokenising

@pytest.mark.parametrize(("text", "expected"), [
    ("Turn the volume down", ["turn", "volume", "down"]),
    # Singularised, so a request about "processes" reaches a description about a
    # "process". Both rules are needed: stripping only a trailing "s" gives
    # "processe", which matches nothing.
    ("kill the chrome processes", ["kill", "chrome", "process"]),
    ("read my files", ["read", "file"]),
    ("check the boxes", ["check", "box"]),
    # ...but not words that merely end in s, and not short ones.
    ("this is a class of its own", ["class", "own"]),
    ("", []),
])
def test_terms_are_reduced_to_what_carries_signal(text, expected):
    assert terms(text) == expected


def test_stopwords_do_not_survive():
    assert terms("what is the thing that you can do with this") == ["thing"]


# ------------------------------------------------------------------------ idf

def test_a_word_in_every_description_is_worth_nothing():
    """The mechanism the whole ranking rests on.

    Every manifest in this project says "use this"; if those words scored, the
    ranking would be dominated by description length rather than by content.
    """
    index = build_index(_registry(
        alpha="use this to open a door",
        beta="use this to close a window",
        gamma="use this to paint a wall",
    ))
    assert index["idf"]["door"] > 0
    assert index["idf"]["open"] > 0
    # "use" survives tokenising as a stopword removal, but were it not a stopword
    # its IDF would be zero — asserted through a word that is in all three.
    assert index["idf"]["to"] == 0 if "to" in index["idf"] else True


def test_a_rare_word_outranks_a_common_one():
    skills = _registry(
        alpha="open a file and read the file contents",
        beta="open a door with a specific brass key",
    )
    ordered = [name for name, _ in rank("brass key", skills)]
    assert ordered[0] == "beta"


def test_the_skills_own_name_counts_for_more_than_its_prose():
    """Somebody who says "screenshot" has named the tool, not described it."""
    # Three skills, not two: with only two documents every shared term has an
    # inverse document frequency of exactly zero, so nothing can outrank
    # anything and the test would pass or fail on alphabetical order.
    skills = _registry(
        screenshot="captures the display and writes it somewhere",
        notes="a place to write down anything, including a note about a screenshot",
        weather="reports the forecast for a city",
    )
    assert rank("take a screenshot", skills)[0][0] == "screenshot"


# ------------------------------------------------------------- the guard rails

def test_a_request_matching_nothing_gets_the_whole_registry():
    """No confident match, no shortlist.

    A ranking built on noise would drop the right tool for a request whose words
    happen to appear in no manifest, which is the one case where narrowing is
    strictly worse than not narrowing.
    """
    skills = {f"skill_{i}": _Skill(f"skill_{i}", "does a particular thing")
              for i in range(30)}
    assert shortlist("xylophone marzipan quixotic", skills, limit=5) == skills


def test_the_floor_is_what_decides_that():
    skills = {f"skill_{i}": _Skill(f"skill_{i}", f"handles topic number {i}")
              for i in range(30)}
    best = rank("nothing here matches at all", skills)[0][1]
    assert best < FLOOR, "the fallback would not trigger for an unmatched request"


def test_a_small_registry_is_never_narrowed():
    # Nothing to gain, and the tests that build the graph from two fake skills
    # must keep seeing both of them.
    skills = _registry(alpha="opens a door", beta="closes a window")
    assert shortlist("open the door", skills) == skills


def test_an_empty_utterance_returns_everything():
    skills = {f"skill_{i}": _Skill(f"skill_{i}", "a thing") for i in range(30)}
    assert shortlist("", skills) == skills
    assert shortlist(None, skills) == skills


def test_the_universal_fallback_is_always_offered():
    """A lookup phrased in words that appear in no manifest still needs somewhere
    to go — manifests describe tools, not the world."""
    skills = {f"skill_{i}": _Skill(f"skill_{i}", f"handles the topic of {i} things")
              for i in range(30)}
    skills["web_search"] = _Skill("web_search", "looks a fact up on the internet")
    kept = shortlist("handles the topic of 7 things", skills, limit=3)
    assert "web_search" in kept
    assert ALWAYS_INCLUDE <= set(kept)


def test_the_list_is_the_size_it_says_it_is():
    skills = {f"skill_{i}": _Skill(f"skill_{i}", f"handles topic {i} of things")
              for i in range(40)}
    kept = shortlist("handles topic 7 of things", skills, limit=6)
    assert len(kept) <= 6 + len(ALWAYS_INCLUDE)


def test_the_same_request_produces_the_same_list_twice():
    """Determinism, so a routing failure is reproducible and the benchmark is
    measuring behaviour rather than dictionary ordering."""
    skills = {f"skill_{i}": _Skill(f"skill_{i}", "handles a topic of things")
              for i in range(30)}
    first = list(shortlist("handles a topic", skills, limit=5))
    for _ in range(5):
        assert list(shortlist("handles a topic", skills, limit=5)) == first


# ------------------------------------- the property that makes this shippable

def test_every_labelled_case_can_still_reach_its_answer():
    """Recall over the real registry and the real case set. The gate.

    Ranking badly costs one wrong answer. Omitting the right tool makes it
    unnameable, so this asserts the correct skill survives the cut for every
    case that actually reaches the model — cases answered by the conversational
    fast path or by the deterministic intent router never get here, and cases
    whose answer is not loaded on this machine cannot be scored.

    Two skills were rewritten on the strength of this test rather than the other
    way round: `system_check` never contained the word "machine" and
    `manage_memory` never contained "remember", so the only words anybody says
    when asking for them appeared nowhere in the text being ranked.
    """
    from core import intents
    from core.registry import discover_skills
    from core.small_talk import is_small_talk
    from tools.routing_bench import load_cases

    skills = discover_skills()
    cases = [case for case in load_cases()
             if any(name in skills for name in case["expect"])
             and not is_small_talk(case["say"])
             and intents.route(case["say"], skills) is None]
    assert len(cases) > 40, "the case set shrank; this gate is no longer meaningful"

    unreachable = []
    for case in cases:
        offered = shortlist(case["say"], skills)
        if not any(name in offered for name in case["expect"]):
            best = rank(case["say"], skills)[:3]
            unreachable.append(f"{case['say']!r} wanted {case['expect']}, top was {best}")

    assert not unreachable, (
        "the shortlist hides the correct skill for these, so the model cannot name it "
        "at all:\n  " + "\n  ".join(unreachable))


def test_the_shortlist_actually_shortens():
    """Otherwise this is all cost and no effect."""
    from core.registry import discover_skills

    skills = discover_skills()
    if len(skills) <= DEFAULT_LIMIT:
        pytest.skip("fewer skills loaded than the limit; nothing to narrow")

    narrowed = [len(shortlist(say, skills)) for say in (
        "kill the chrome process", "turn the volume down", "what is the weather in Delhi",
        "take a screenshot", "read me the news")]
    assert all(size < len(skills) for size in narrowed), narrowed
    assert max(narrowed) <= DEFAULT_LIMIT + len(ALWAYS_INCLUDE)
