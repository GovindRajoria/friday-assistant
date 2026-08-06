# tools/routing_bench.py
"""Score which tool the assistant reaches for, against a labelled request set.

The number this project did not have. Forty-five skills were shipped with no
measurement of whether the model picks the right one, and one batch of
additions degraded routing badly enough that "hello" ran twenty tool calls —
caught by a human reading a transcript, not by anything in the suite.

WHAT IT MEASURES, precisely: the FIRST routing decision for one utterance.
Not the answer, not the chain. Routing is where the failure lives — a wrong
first tool is a wrong turn however good the rest of the machinery is — and a
first decision is one model call, which keeps a full run cheap enough to
actually re-run after every change.

It goes through the same two stages a real turn does, in the same order:

  1. `core.small_talk.is_small_talk`, the conversational fast path. A case
     expecting `converse` is scored on this and never reaches the model,
     because in production it never would either.
  2. `core.nodes.reason.reason_node` against the live registry and the live
     system prompt. Not a reimplementation of it — the actual node, so a
     change to the prompt or the schema shows up here without this file being
     touched.

WHAT IT IS NOT: a CI gate. It needs Ollama, a loaded model, and about a
minute; and its result moves by a case or two between runs even at
temperature 0.1. Asserting a threshold in the suite would make the suite
flaky and dependent on a running model. It is a tool you run deliberately,
before and after changing the skill set, and whose two numbers you compare.

    python -m tools.routing_bench                       # score, print a table
    python -m tools.routing_bench --save before.json    # keep it
    python -m tools.routing_bench --compare before.json # what moved

A case whose every expected skill is absent from the registry is reported
as *unavailable* rather than wrong, and left out of the comparable score.
That is what makes a before/after honest across a change that adds skills:
without it, adding `world_time` would "improve" routing purely because the
answer key started being answerable, and a real regression elsewhere could
hide inside that gain.
"""
import argparse
import json
import sys
import time
from pathlib import Path

CASES_PATH = Path(__file__).with_name("routing_cases.yaml")

# The two answers that are not skill names. `converse` is decided before the
# model is asked at all; `none` is the schema's no-tool sentinel.
CONVERSE = "converse"


def load_cases(path=CASES_PATH):
    """Flatten the grouped YAML into (group, say, expect) triples."""
    import yaml

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    flat = []
    for group in document:
        for case in group["cases"]:
            expect = case["expect"]
            if not isinstance(expect, list):
                raise ValueError(f"{case['say']!r}: expect must be a list, got {type(expect).__name__}")
            flat.append({"group": group["group"], "say": case["say"], "expect": expect})
    return flat


def availability(expect, loaded) -> bool:
    """Could this case possibly be got right by the registry as it stands?

    True for anything expecting `none` or `converse` — those need no skill —
    and for any case naming at least one loaded skill. False means the answer
    key is asking for a tool this build does not have.
    """
    return any(name in (CONVERSE, "none") or name in loaded for name in expect)


def decide(say, active_skills):
    """One routing decision for one utterance, through the real code path.

    Returns (chosen, thought, decided_by). `decided_by` is reported because two
    of the three stages never ask the model, and a headline accuracy figure that
    hid that would be flattering nonsense: "9/9 on greetings" means something
    quite different when a regex decided all nine. The stages and their order
    are core/graph.py's `_entry_point`, not a copy of its logic — the two
    predicates it calls are the two called here.
    """
    from core import intents
    from core.nodes.reason import reason_node
    from core.small_talk import is_small_talk

    if is_small_talk(say):
        return CONVERSE, "", "fast path"

    routed = intents.route(say, active_skills)
    if routed is not None:
        return routed[0], "", "dispatch"

    result = reason_node({"user_input": say, "memory_buffer": "", "screen_context": ""}, active_skills)
    return result.get("action") or "none", result.get("thought", ""), "model"


def run(cases, active_skills, show_thoughts=False):
    loaded = set(active_skills)
    results = []
    for index, case in enumerate(cases, start=1):
        available = availability(case["expect"], loaded)
        started = time.perf_counter()
        try:
            chose, thought, decided_by = decide(case["say"], active_skills)
        except Exception as error:  # noqa: BLE001 — one dead case must not lose the whole run
            chose, thought, decided_by = f"<error: {error}>", "", "error"
        elapsed = time.perf_counter() - started

        correct = chose in case["expect"]
        results.append({**case, "chose": chose, "correct": correct, "decided_by": decided_by,
                        "available": available, "seconds": round(elapsed, 2)})

        mark = "ok  " if correct else ("n/a " if not available else "MISS")
        line = f"[{index:>3}/{len(cases)}] {mark} {case['say'][:46]:<46} -> {chose}"
        if not correct:
            line += f"   (wanted {'/'.join(case['expect'])})"
        print(line, flush=True)
        if show_thoughts and thought:
            print(f"            thought: {thought[:150]}", flush=True)
    return results


def summarise(results):
    """Overall, comparable and per-group scores.

    `comparable` is the headline: every case this build could in principle get
    right. `overall` includes the unavailable ones and is only useful as a
    reminder of how much of the answer key this build cannot reach.
    """
    comparable = [r for r in results if r["available"]]
    groups = {}
    for entry in results:
        bucket = groups.setdefault(entry["group"], {"hit": 0, "of": 0, "unavailable": 0})
        if not entry["available"]:
            bucket["unavailable"] += 1
            continue
        bucket["of"] += 1
        bucket["hit"] += 1 if entry["correct"] else 0
    # Split by who decided, so the headline cannot be mistaken for a model
    # score. `model` is the only figure that says anything about routing over the
    # full enum; the other two say whether the gates in front of it are aimed
    # correctly, which is a different and also useful thing to know.
    by_stage = {}
    for entry in comparable:
        bucket = by_stage.setdefault(entry["decided_by"], {"hit": 0, "of": 0})
        bucket["of"] += 1
        bucket["hit"] += 1 if entry["correct"] else 0

    return {
        "cases": len(results),
        "comparable": len(comparable),
        "unavailable": len(results) - len(comparable),
        "hit": sum(1 for r in comparable if r["correct"]),
        "accuracy": round(100 * sum(1 for r in comparable if r["correct"]) / max(1, len(comparable)), 1),
        "seconds": round(sum(r["seconds"] for r in results), 1),
        "groups": groups,
        "by_stage": by_stage,
    }


def report(summary, results):
    print("\n" + "=" * 78)
    print(f"  {summary['hit']}/{summary['comparable']} = {summary['accuracy']}% "
          f"of the cases this build can reach   ({summary['unavailable']} need a skill "
          f"that is not loaded, {summary['seconds']}s total)")
    print("=" * 78)
    for stage, score in sorted(summary.get("by_stage", {}).items()):
        percent = round(100 * score["hit"] / max(1, score["of"]))
        print(f"    decided by {stage:<10} {score['hit']}/{score['of']} {percent:>4}%")
    print("-" * 78)
    for group, score in summary["groups"].items():
        if score["of"] == 0:
            print(f"    {'—':>7}  {group}  ({score['unavailable']} unavailable)")
            continue
        percent = round(100 * score["hit"] / score["of"])
        tail = f"  ({score['unavailable']} unavailable)" if score["unavailable"] else ""
        print(f"    {score['hit']}/{score['of']} {percent:>4}%  {group}{tail}")

    misses = [r for r in results if r["available"] and not r["correct"]]
    if misses:
        print("\n  Misses, which are the only interesting part:")
        for miss in misses:
            print(f"    {miss['say']!r}\n        chose {miss['chose']}, wanted {'/'.join(miss['expect'])}")


def compare(previous_path, results, summary):
    """What moved. The delta is the whole reason this file exists."""
    previous = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    before = {entry["say"]: entry for entry in previous["results"]}
    print("\n" + "-" * 78)
    print(f"  Against {previous_path}: "
          f"{previous['summary']['accuracy']}% -> {summary['accuracy']}% "
          f"({previous['summary']['hit']}/{previous['summary']['comparable']} -> "
          f"{summary['hit']}/{summary['comparable']} comparable)")

    regressions, fixes, newly = [], [], []
    for entry in results:
        was = before.get(entry["say"])
        if was is None:
            newly.append(entry)
            continue
        if was["correct"] and not entry["correct"]:
            regressions.append((was, entry))
        elif not was["correct"] and entry["correct"]:
            fixes.append((was, entry))

    for label, rows in (("REGRESSED", regressions), ("fixed", fixes)):
        for was, now in rows:
            note = "" if was["available"] else "  (was unavailable)"
            print(f"  {label}: {now['say']!r}: {was['chose']} -> {now['chose']}{note}")
    if newly:
        print(f"  {len(newly)} case(s) not in the earlier run: "
              + ", ".join(repr(entry["say"]) for entry in newly[:6]))
    if not (regressions or fixes or newly):
        print("  Nothing moved.")
    return regressions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--save", metavar="PATH", help="write the full result set to a JSON file")
    parser.add_argument("--compare", metavar="PATH", help="diff against a saved run")
    parser.add_argument("--group", action="append", help="only run groups whose name contains this")
    parser.add_argument("--thoughts", action="store_true", help="print each stated plan, to see why it missed")
    args = parser.parse_args(argv)

    from core.registry import discover_skills

    cases = load_cases()
    if args.group:
        cases = [c for c in cases if any(fragment.lower() in c["group"].lower() for fragment in args.group)]
        if not cases:
            print("No cases matched.", file=sys.stderr)
            return 2

    active_skills = discover_skills()
    print(f"\n{len(cases)} cases against {len(active_skills)} loaded skills\n")

    results = run(cases, active_skills, show_thoughts=args.thoughts)
    summary = summarise(results)
    report(summary, results)

    if args.save:
        Path(args.save).write_text(
            json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
        print(f"\n  Saved to {args.save}")
    if args.compare:
        compare(args.compare, results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
