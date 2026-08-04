# tests/test_skill_routing_surface.py
"""The manifest `description` is the routing logic, so it gets a gate.

`tools/check_manifests.py` enforces that a description exists and is at least 40
characters. It cannot enforce that two descriptions are *distinguishable*, which is
the failure that matters once there are 45 skills: nothing breaks, no gate goes
red, and the model simply picks `manage_files` when it wanted `read_document`.

This project has no measured tool-selection accuracy, so these tests cannot prove
routing is good. What they can do is pin the disambiguations that were deliberately
written — the pattern `explain_architecture` established with "Use core_identity
instead for WHAT you can do" — so that a later edit which drops one fails here
rather than silently degrading which tool gets chosen.

Only pairs that genuinely compete for the same phrasing are listed. Requiring every
status skill to name every other would bloat descriptions that are themselves part
of the prompt budget.
"""
import contextlib
import io

import pytest
from core.registry import discover_skills

# (skill, sibling it must steer away from) — asserted in the stated direction,
# because that is the direction the ambiguous request arrives from.
MUST_STEER = [
    ("read_document", "read_webpage"),      # a file on disk vs a URL
    ("read_document", "manage_files"),      # reading a document vs listing a directory
    ("read_spreadsheet", "read_document"),  # rows and totals vs prose
    ("search_files", "search_code"),        # workspace documents vs a source tree
    ("search_files", "manage_files"),       # find anywhere vs list a known directory
    ("search_code", "search_files"),
    ("ocr_screen", "describe_screen"),      # exact characters vs an unreliable impression
    ("ocr_screen", "screenshot"),           # text out vs a picture saved
    ("screenshot", "describe_screen"),
    ("annotate_image", "scan_environment"), # a file vs the webcam
    ("skill_health", "diagnose_self"),      # own skills vs the machine around them
    ("diagnose_self", "skill_health"),
    ("diagnose_self", "disk_report"),       # the broad sweep vs one specific question
    ("diagnose_self", "network_status"),
    ("diagnose_self", "gpu_status"),
    ("explain_last_turn", "explain_architecture"),   # what it just did vs how it works
    ("task_list", "reminders"),             # no time vs interrupt me at a time
    ("journal", "task_list"),               # what happened vs what is still to do
    ("journal", "reminders"),
    ("calendar", "reminders"),
    ("open_url", "read_webpage"),           # show a human vs fetch text to answer from
    ("run_command", "run_tests"),           # the general escape hatch vs the specific skill
    ("run_command", "inspect_repo"),
    ("run_tests", "inspect_repo"),          # executing vs reading
    ("inspect_repo", "search_code"),
    ("check_camera_stream", "scan_environment"),  # a network camera vs this webcam
    ("disk_report", "system_check"),        # storage vs CPU and memory
    ("gpu_status", "system_check"),
]


@pytest.fixture(scope="module")
def skills():
    # Discovery prints a line per module; silenced so the test output stays useful.
    with contextlib.redirect_stdout(io.StringIO()):
        return discover_skills()


@pytest.mark.parametrize("skill_name, sibling", MUST_STEER)
def test_a_competing_skill_is_named_in_the_description(skills, skill_name, sibling):
    if skill_name not in skills:
        pytest.skip(f"{skill_name} is not loaded on this machine")
    if sibling not in skills:
        pytest.skip(f"{sibling} is not loaded on this machine")

    description = skills[skill_name].manifest["description"]

    assert sibling in description, (
        f"{skill_name}'s description does not mention {sibling}. These two compete for "
        "the same kind of request, and the description is the only information the model "
        "has when choosing between them."
    )


def test_a_skill_with_a_known_competitor_steers_rather_than_only_describing(skills):
    """A description that only describes cannot steer.

    Scoped to skills that actually appear in MUST_STEER, which is the real
    invariant. Asserting it over every skill would be a style rule: several
    older skills — window_control, draft_document, media_control — have no
    competitor to be confused with, and padding their descriptions to satisfy a
    test would spend prompt budget for nothing.
    """
    competing = {name for pair in MUST_STEER for name in pair}
    weak = []
    for name in sorted(competing & skills.keys()):
        description = skills[name].manifest["description"]
        steers = ("use " in description.lower()
                  or any(other in description for other in skills if other != name))
        if not steers:
            weak.append(name)

    assert not weak, f"these descriptions give the model no guidance on when to choose them: {weak}"


def test_terminal_skills_say_that_their_answer_is_complete(skills):
    """`terminal` ends the turn. If the description does not say so, the model
    cannot know its plan is about to be cut short."""
    silent = [name for name, skill in skills.items()
              if skill.manifest.get("terminal")
              and "complete" not in skill.manifest["description"].lower()]

    assert not silent, f"terminal skills that do not say their answer is complete: {silent}"


def test_destructive_skills_say_confirmation_is_required(skills):
    """The human sees a prompt; the model should have said one was coming."""
    silent = [name for name, skill in skills.items()
              if skill.manifest.get("destructive")
              and "confirm" not in skill.manifest["description"].lower()]

    assert not silent, f"destructive skills that do not mention confirmation: {silent}"


def test_no_two_skills_share_a_description(skills):
    seen = {}
    for name, skill in skills.items():
        description = skill.manifest["description"]
        assert description not in seen, f"{name} and {seen[description]} have identical descriptions"
        seen[description] = name
