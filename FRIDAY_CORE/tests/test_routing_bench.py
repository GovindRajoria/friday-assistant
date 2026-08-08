"""The routing benchmark's own arithmetic, and its answer key.

`tools/routing_bench.py` needs Ollama and three and a half minutes, so it is not
a CI gate and nothing here runs it. What is testable without a model is
everything that turns decisions into the number people will quote — and that
number is the whole artifact, so the arithmetic behind it should not be the one
part nobody checks.

The answer key gets a test for the same reason `intents.py` does: a misspelled
skill name in `expect` does not fail anything. It silently marks that case
*unavailable*, drops it out of the denominator, and flatters the score.
"""
import pytest
from tools import routing_bench as bench

# Named in routing_cases.yaml before they exist, so their groups start counting
# the moment they are built. This list is therefore a to-do, and it is empty:
# `world_time` and `voice_control` were written on 2026-08-07 and the test below
# is what said so — it failed the moment the files appeared, which is exactly the
# job it was left here to do. Anything added here again is a promise to build it.
NOT_BUILT_YET: set[str] = set()


def test_the_answer_key_only_names_skills_that_exist():
    from tests.test_intents import _declared_manifests

    declared = set(_declared_manifests())
    expected = {name for case in bench.load_cases() for name in case["expect"]}
    unknown = expected - declared - {bench.CONVERSE, "none"} - NOT_BUILT_YET
    assert not unknown, (
        "routing_cases.yaml expects skills that do not exist, so those cases are silently "
        f"uncounted rather than failing: {sorted(unknown)}")


def test_the_not_built_yet_list_stays_honest():
    """A name here that now exists is a case that should be scoring and is not."""
    from tests.test_intents import _declared_manifests

    declared = set(_declared_manifests())
    already = NOT_BUILT_YET & declared
    assert not already, (
        f"{sorted(already)} exist now — drop them from NOT_BUILT_YET so the bench counts them")


def test_every_case_has_a_non_empty_list_of_acceptable_answers():
    cases = bench.load_cases()
    assert len(cases) > 60, "the case set has shrunk unexpectedly"
    for case in cases:
        assert case["expect"], f"{case['say']!r} has no acceptable answer"
        assert case["say"].strip(), "a case with no utterance"
        assert case["say"] == case["say"].strip()


def test_expect_must_be_a_list_not_a_bare_string(tmp_path):
    """A bare string would iterate character by character and match nothing.

    Worth a hard failure rather than a silently-zero group, which is what a
    one-word `expect:` written without a dash would otherwise produce.
    """
    path = tmp_path / "cases.yaml"
    path.write_text("- group: g\n  cases:\n    - say: hello\n      expect: converse\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        bench.load_cases(path)


@pytest.mark.parametrize(("expect", "loaded", "available"), [
    (["weather"], {"weather"}, True),
    (["weather"], set(), False),
    # Two acceptable answers, one of them loaded: still reachable.
    (["inspect_repo", "run_command"], {"run_command"}, True),
    # Neither needs a skill at all.
    (["none"], set(), True),
    (["converse"], set(), True),
    # The case that matters for a before/after across added skills.
    (["world_time"], {"weather"}, False),
])
def test_availability_decides_whether_a_case_can_be_scored(expect, loaded, available):
    assert bench.availability(expect, loaded) is available


def _result(say, group, expect, chose, available=True, decided_by="model"):
    return {"say": say, "group": group, "expect": expect, "chose": chose,
            "correct": chose in expect, "available": available,
            "decided_by": decided_by, "seconds": 1.0}


def test_the_headline_excludes_cases_this_build_cannot_reach():
    """Otherwise adding a skill "improves" routing purely by making the answer
    key answerable, and a real regression elsewhere hides inside that gain."""
    summary = bench.summarise([
        _result("a", "g", ["weather"], "weather"),
        _result("b", "g", ["read_news"], "web_search"),
        _result("c", "g", ["world_time"], "none", available=False),
    ])
    assert summary["cases"] == 3
    assert summary["comparable"] == 2
    assert summary["unavailable"] == 1
    assert summary["hit"] == 1
    assert summary["accuracy"] == 50.0


def test_the_score_is_split_by_which_stage_decided_it():
    """"100% on greetings" means something quite different when a regex decided
    all of them, so the headline must never be quotable without that split."""
    summary = bench.summarise([
        _result("hello", "chat", ["converse"], "converse", decided_by="fast path"),
        _result("who are you", "self", ["core_identity"], "core_identity", decided_by="dispatch"),
        _result("weather", "web", ["weather"], "weather"),
        _result("news", "web", ["read_news"], "screenshot"),
    ])
    assert summary["by_stage"]["fast path"] == {"hit": 1, "of": 1}
    assert summary["by_stage"]["dispatch"] == {"hit": 1, "of": 1}
    assert summary["by_stage"]["model"] == {"hit": 1, "of": 2}


def test_an_all_unavailable_group_does_not_divide_by_zero():
    summary = bench.summarise([_result("x", "voice", ["voice_control"], "none", available=False)])
    assert summary["accuracy"] == 0.0
    assert summary["groups"]["voice"] == {"hit": 0, "of": 0, "unavailable": 1}


def test_comparing_two_runs_scores_the_cases_both_could_reach(tmp_path, capsys):
    """The like-for-like figure, which is the only one that reads correctly
    across a change that adds skills.

    Overall accuracy is computed against each run's own denominator, so a run
    that can reach more of the answer key is not comparable to one that could
    not. Here the earlier run could not reach "c" at all and got "b" wrong; the
    later run reaches everything. Overall goes up partly because a new skill
    exists — the intersection is what says whether *routing* changed.
    """
    before = [
        _result("a", "g", ["weather"], "weather"),
        _result("b", "g", ["read_news"], "screenshot"),
        _result("c", "g", ["world_time"], "none", available=False),
    ]
    path = tmp_path / "before.json"
    path.write_text(__import__("json").dumps(
        {"summary": bench.summarise(before), "results": before}), encoding="utf-8")

    after = [
        _result("a", "g", ["weather"], "weather"),
        _result("b", "g", ["read_news"], "read_news"),
        _result("c", "g", ["world_time"], "world_time"),
    ]
    regressions = bench.compare(path, after, bench.summarise(after))
    printed = capsys.readouterr().out

    assert regressions == []
    assert "fixed" in printed
    # Overall: 1/2 -> 3/3. Like-for-like on the two cases both runs could reach:
    # 1/2 -> 2/2, which is the honest statement about routing.
    assert "like for like" in printed
    assert "1/2 -> 2/2" in printed


def test_a_regression_is_named_when_comparing(tmp_path, capsys):
    before = [_result("a", "g", ["weather"], "weather")]
    path = tmp_path / "before.json"
    path.write_text(__import__("json").dumps(
        {"summary": bench.summarise(before), "results": before}), encoding="utf-8")

    after = [_result("a", "g", ["weather"], "screenshot")]
    regressions = bench.compare(path, after, bench.summarise(after))
    assert [entry["say"] for _, entry in regressions] == ["a"]
    assert "REGRESSED" in capsys.readouterr().out
