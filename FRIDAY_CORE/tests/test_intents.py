"""The self-knowledge router, broken on purpose from both directions.

A deterministic route is a gate, and this project's rule for a new gate is that
it has to be made to fail before it is trusted. This one has two failure modes
with very different costs:

  * A MISS sends a question about FRIDAY to the model, which answers it from a
    generic self-image. Costs one wrong answer, and the model has been doing
    exactly that all along.
  * A FALSE POSITIVE steals a real request — "how do you say good evening in
    Hindi" answered by reading out the architecture document. That is worse: it
    is confidently, visibly wrong, and no amount of rephrasing gets the user
    past it, because a regex does not change its mind.

So the second half of this file is longer than the first, and the cases in it
were written by going through the router's patterns looking for sentences that
start the same way and mean something else.
"""
import pytest
from core import intents


@pytest.mark.parametrize(("said", "expected"), [
    # Identity — the four phrasings from the live transcript and the benchmark.
    ("who are you", "core_identity"),
    ("Who are you?", "core_identity"),
    ("what can you do", "core_identity"),
    ("what are your abilities", "core_identity"),
    ("what are your capabilities", "core_identity"),
    ("what skills do you have", "core_identity"),
    ("list your tools", "core_identity"),
    ("tell me about yourself", "core_identity"),
    ("what is your purpose", "core_identity"),
    ("whats your name", "core_identity"),
    # Architecture, each to the section that answers it.
    ("how do you work", "explain_architecture"),
    ("what is langgraph for", "explain_architecture"),
    ("how do you hear me", "explain_architecture"),
    ("how do you see", "explain_architecture"),
    ("how do you keep me safe", "explain_architecture"),
    ("how do you decide what to do", "explain_architecture"),
    ("explain your architecture", "explain_architecture"),
    ("how were you built", "explain_architecture"),
    # The last turn.
    ("what did you just do", "explain_last_turn"),
    ("why did you do that", "explain_last_turn"),
    ("what tools did you use", "explain_last_turn"),
    ("how did you work that out", "explain_last_turn"),
    # Health.
    ("are you ok", "diagnose_self"),
    ("are you working properly", "diagnose_self"),
    ("is anything broken", "diagnose_self"),
    ("run a self diagnostic", "diagnose_self"),
    ("are any of your skills broken", "skill_health"),
    ("how many skills do you have", "skill_health"),
])
def test_questions_about_itself_never_reach_the_model(said, expected):
    routed = intents.route(said)
    assert routed is not None, f"{said!r} fell through to the model"
    assert routed[0] == expected


@pytest.mark.parametrize(("said", "topic"), [
    ("how do you hear me", "voice"),
    ("how do you speak", "voice"),
    ("how do you see the screen", "vision"),
    ("how do you keep me safe", "safety"),
    ("what is langgraph for", "reasoning"),
    ("how do you decide", "reasoning"),
    ("how do your skills work", "skills"),
    ("how do you work", "overview"),
])
def test_each_architecture_question_lands_on_the_section_that_answers_it(said, topic):
    """A topic is not decoration: the skill returns one section verbatim, so a
    question about hearing that fetches the overview is a non-answer."""
    routed = intents.route(said)
    assert routed == ("explain_architecture", {"topic": topic})


@pytest.mark.parametrize("said", [
    # The one that would be most embarrassing: a translation request that starts
    # with the same three words as an architecture question.
    "how do you say good evening in hindi",
    "how do you say thank you in french",
    # Real requests that open like an identity question.
    "what can you do about the disk being full",
    "who are you emailing about the outage",
    "what tools do I need to install for this repo",
    "tell me about yourself and then read the news",
    # Someone else's health, not FRIDAY's.
    "is the camera broken",
    "is the network broken",
    "are any of the cameras offline",
    "what is wrong with the build",
    # The machine's state, which has its own skills.
    "how is the machine doing",
    "how much disk space do you have",
    "what is using all my memory",
    # Things that merely mention the word skills or tools.
    "write a document listing my skills",
    "search for langgraph documentation",
    "read me the langgraph release notes",
    # A question about the last turn's *subject*, not the turn.
    "what did you find out about the price",
    "why did the test fail",
])
def test_a_real_request_is_never_stolen_by_the_router(said):
    assert intents.route(said) is None, f"{said!r} was wrongly claimed by the intent router"


def test_address_and_politeness_are_stripped_from_the_ends_only():
    """"friday, who are you please" is the same question as "who are you"."""
    assert intents.route("friday who are you please") == ("core_identity", {})
    assert intents.route("hey friday, what can you do?") == ("core_identity", {})
    # But not from the middle: removing "me" anywhere would make this match
    # "what can you do".
    assert intents.route("what can you tell me about the repo") is None


def test_an_empty_or_punctuation_only_message_routes_nowhere():
    for said in ("", "   ", "???", "..."):
        assert intents.route(said) is None


def test_a_rule_naming_an_unloaded_skill_falls_through_to_the_model():
    """`skills.disabled` has to keep meaning what it says.

    Dispatching to a skill that is not in the registry would raise inside
    act_node instead of simply being answered less well, so an absent skill
    hands the message back to the ordinary loop.
    """
    assert intents.route("who are you", available={"core_identity": object()}) is not None
    assert intents.route("who are you", available={}) is None
    assert intents.route("who are you", available={"weather": object()}) is None


def _declared_manifests():
    """Every skill's {name: manifest-ish dict} read statically off the source.

    An AST parse rather than `discover_skills()`, which would need every skill's
    dependencies installed — the same reason tools/check_manifests.py is static.
    Only literal keys and values are recovered, which is all any manifest in this
    project uses.
    """
    import ast

    from core.config import PROJECT_ROOT

    found = {}
    for path in (PROJECT_ROOT / "skills").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            literal = {}
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                    literal[key.value] = value.value
            if isinstance(literal.get("name"), str) and "description" in literal:
                found[literal["name"]] = literal
    assert found, "no manifests were parsed at all — the AST walk is broken, not the router"
    return found


def test_every_rule_names_a_skill_that_exists_in_the_project():
    """A typo in a skill name here is a route to nothing."""
    declared = _declared_manifests()
    named = {skill for _, skill, _ in intents.RULES}
    assert named <= set(declared), f"intents.py routes to skills that do not exist: {named - set(declared)}"


def test_every_dispatched_skill_ends_the_turn():
    """`terminal: True` on all of them is an invariant, not a coincidence.

    `dispatch_node` sets no `messages`, because there is no model exchange to
    record. `act` then appends its observation to that empty list, so a
    NON-terminal skill would route on to `reason` holding one observation and no
    system prompt and no question — and `reason` only builds those when the list
    is empty, so it could not recover. All five are terminal today. This is what
    stops a sixth rule from quietly introducing that state.
    """
    declared = _declared_manifests()
    not_terminal = sorted(skill for _, skill, _ in intents.RULES
                          if not declared[skill].get("terminal"))
    assert not not_terminal, (
        "intents.py dispatches to skills that do not end the turn, which would reach `reason` "
        f"with no system prompt: {not_terminal}")


def test_small_talk_is_checked_before_this_router():
    """Precedence in core/graph.py's entry point, asserted where it can be read.

    "are you ok" matches a health rule here and is also entirely made of
    conversational words, and conversation is the right reading of it — nobody
    asking that wants a diagnostic report. The graph checks small talk first, so
    this router only ever sees what conversation did not claim. Asserted because
    swapping the two lines would change the answer to a question people ask.
    """
    from core.graph import _entry_point
    from core.small_talk import is_small_talk

    assert is_small_talk("are you ok")
    assert intents.route("are you ok") is not None

    registry = {name: object() for name in
                ("core_identity", "explain_architecture", "explain_last_turn",
                 "diagnose_self", "skill_health", "weather")}
    choose = _entry_point(registry)
    assert choose({"user_input": "are you ok"}) == "converse"
    assert choose({"user_input": "are you working properly"}) == "dispatch"
    assert choose({"user_input": "who are you"}) == "dispatch"
    assert choose({"user_input": "hello"}) == "converse"
    assert choose({"user_input": "what is the weather"}) == "reason"


def test_the_broad_identity_rules_do_not_shadow_the_narrow_ones():
    """Rule order is load-bearing.

    "what did you just do" also matches the much broader "what do you do", so
    the narrow rule has to be earlier in the list. Asserting the behaviour
    rather than the index, so reordering is free as long as the outcome holds.
    """
    assert intents.route("what did you just do")[0] == "explain_last_turn"
    assert intents.route("what do you do")[0] == "core_identity"


@pytest.mark.parametrize("said", [
    # Verbatim from the first live use, which is why this rule exists.
    "what's the time",
    "whats the time",
    "what is the time",
    "what time is it",
    "friday what time is it",
    "hey friday, what's the time?",
    "do you have the time",
    "the time please",
    "what is the date",
    "what's today's date",
    "what date is it",
    "what day is it",
    "what day is it today",
    "what is the day today",
])
def test_the_clock_is_answered_without_asking_the_model(said):
    """Asked "what's the time" on 2026-08-07, the model stated the correct answer
    in its own thought — from the timestamp now in every prompt — and then called
    `world_time` with place "Delhi, India" regardless, so the reply the operator
    got was an error about timezone names. OPERATING RULE 1a had been written that
    morning to prevent exactly that, and was ignored.

    `world_time` with no parameters reports the local date and time, so
    dispatching here is deterministic and right.
    """
    routed = intents.route(said)
    assert routed is not None, f"{said!r} fell through to the model"
    assert routed == ("world_time", {})


@pytest.mark.parametrize("said", [
    # A place or a date means there IS something to look up, and the model has to
    # fill the parameter in. These must reach the ordinary loop.
    "what time is it in tokyo",
    "what is the time in london",
    "what day is it in japan",
    "how many days until christmas",
    "what date is the meeting",
    "what time is the meeting",
    "what time does the shop open",
    "what is the date of the release",
    "tell me the time in new york",
    # Not about the clock at all.
    "what is the time complexity of this function",
    "how long did that take",
    "set a reminder for 5pm",
])
def test_a_clock_question_with_somewhere_to_look_still_reaches_the_model(said):
    assert intents.route(said) is None, f"{said!r} was wrongly claimed by the intent router"


@pytest.mark.parametrize(("said", "expected"), [
    # `normalise` turns an apostrophe into a space, so these arrived as
    # "what s your name" — a bare "s" between two words every pattern expects to
    # be adjacent. Every contraction silently missed for a day because of it, and
    # the clock rule is what finally exposed it.
    ("what's your name", "core_identity"),
    ("what's your purpose", "core_identity"),
    ("who're you", "core_identity"),
    ("what's the time", "world_time"),
    ("what's today's date", "world_time"),
    ("what's broken", "diagnose_self"),
    # The typographic apostrophe a phone or a word processor inserts.
    ("what\u2019s the time", "world_time"),
    ("what\u2019s your name", "core_identity"),
])
def test_a_contraction_routes_the_same_as_its_expansion(said, expected):
    routed = intents.route(said)
    assert routed is not None, f"{said!r} fell through to the model"
    assert routed[0] == expected
