# core/intents.py
"""Questions about FRIDAY itself, routed in Python rather than by the model.

This is the third time this project has reached the same conclusion, and the
first time it was reached from a measurement rather than from reading one bad
transcript. `tools/routing_bench.py`, forty-five skills loaded:

  * Before `needs_tool` existed, the eleven self-knowledge cases scored 7/11 —
    and the four misses were things like "how do you keep me safe" choosing
    `network_status` and "what is LangGraph for" choosing `describe_screen`.
  * After `needs_tool` was added, those same eleven scored **1/11**. Not
    because routing got worse, but because the model now says "no, I need no
    tool" for every question about itself. Asked who it is, it is certain it
    already knows.

Both numbers are the same finding from opposite directions: the model has a
confident, generic, wrong self-image, and no wording in a prompt or a schema
dislodges it. `core/prompts.py` has spent four separate rules asking (4, 4a,
plus the guardrails) and `docs/DESIGN.md` records the answer it invented for
LangGraph — "a graph-based natural language processing library for reasoning
about relationships between concepts". It is not.

So this class of question does not go to the model at all. Four skills already
read the true answer off this machine — the loaded registry, the architecture
document, the turn log, the health probes — and every one of them is `terminal`,
meaning its output *is* the reply. Choosing between four known tools by pattern
is something Python does exactly right and the model does at 9%.

WHY ONLY THIS CLASS. The same benchmark showed the volume commands routing to
`manage_settings` and "lock the screen" routing to `screenshot`, and those are
NOT here. They are description collisions — two manifests that read alike — and
the fix is to make the descriptions say which is which, because that generalises
to phrasings nobody wrote a pattern for. Self-knowledge is different in kind:
the model is not confused between two options, it is confident it needs neither.
A pattern is the only thing that beats a belief.

FALSE POSITIVES ARE THE REAL RISK, so every pattern is matched against the
*whole* normalised message with `fullmatch`, after a small set of politeness and
address words is stripped from either end. "Who are you" routes; "who are you
going to email about the disk" does not match anything and goes to the ordinary
loop. "How do you say good evening in Hindi" must never reach the architecture
document, and `tests/test_intents.py` holds that case and a dozen like it from
both directions.
"""
import re

from core.small_talk import normalise

# Stripped before matching, so "friday, who are you please" is the same message
# as "who are you". Split by end, which is not fussiness — a shared list got all
# three of its first bugs from words that are filler at one end and content at
# the other. "ok" leads ("ok friday, who are you") and trails as a real word
# ("are you ok"), where trimming it left "are you" and matched nothing. "tell me"
# leads ("tell me about yourself") and was being eaten from the front, so the
# pattern that exists for that exact sentence never saw it.
LEAD_TRIM = {
    "friday", "hey", "hi", "hello", "ok", "okay", "so", "and", "well", "just",
    "please", "um", "uh", "erm", "actually", "now", "sir",
}
TAIL_TRIM = {"please", "sir", "friday", "then", "actually", "exactly", "thanks"}

# The architecture document's own section slugs, which is what the skill takes.
# A topic that is not one of them falls back to the overview inside the skill,
# so a mapping that guesses slightly wrong still answers approximately.
OVERVIEW, REASONING, VOICE, VISION, SAFETY, SKILLS = (
    "overview", "reasoning", "voice", "vision", "safety", "skills")

# (pattern, skill, params). Ordered: the first full match wins, so anything
# narrow has to come before anything broad that would also match it.
#
# Written against how the questions are actually spoken. "What can you do" is
# nine times more common out loud than "enumerate your capabilities", and a
# pattern set built from careful prose matches nothing anybody says.
RULES: list[tuple[str, str, dict]] = [
    # --- what it just did. Before the identity rules: "what did you do" would
    # otherwise be swallowed by "what do you do".
    (r"what did you (?:just )?do(?: just now| last| there)?", "explain_last_turn", {}),
    (r"why did you (?:do|choose|pick|call|use) (?:that|it|those|them)", "explain_last_turn", {}),
    (r"(?:explain|walk me through) (?:your |the )?last (?:turn|answer|reply|step|steps)",
     "explain_last_turn", {}),
    (r"what (?:tools?|skills?) did you (?:just )?use", "explain_last_turn", {}),
    (r"how did you (?:get|work|figure) (?:that|it) out", "explain_last_turn", {}),
    (r"what (?:was|were) your (?:last )?steps?", "explain_last_turn", {}),

    # --- whether it is working. Before identity for the same reason: "are you
    # ok" is not "who are you", but "what is wrong" would match nothing else.
    (r"are you (?:ok|okay|alright|well|healthy|working|working properly|broken|fine|good)",
     "diagnose_self", {}),
    (r"(?:are|is) (?:any of )?your skills? (?:broken|working|ok|okay|failing|loaded)",
     "skill_health", {}),
    (r"(?:did|have) (?:any|all) (?:of )?your skills? (?:fail|failed|load|loaded)", "skill_health", {}),
    (r"(?:what|anything) (?:is |s )?(?:broken|wrong|failing|not working)", "diagnose_self", {}),
    (r"is (?:anything|everything) (?:broken|wrong|failing|working|ok|okay)", "diagnose_self", {}),
    (r"(?:run (?:a |the )?)?(?:self.?)?(?:diagnostics?|diagnose yourself|health check)",
     "diagnose_self", {}),
    (r"how many (?:tools?|skills?) (?:do you have|are loaded|have you got)", "skill_health", {}),

    # --- how it is built. Each maps to the section of docs/ARCHITECTURE.md that
    # actually answers it, rather than dumping the overview at everything.
    (r"how do you (?:hear|listen to|understand)(?: me| what i say)?", "explain_architecture",
     {"topic": VOICE}),
    (r"how do you (?:speak|talk)(?: to me)?", "explain_architecture", {"topic": VOICE}),
    (r"how do you (?:see|watch)(?: me| the screen| anything)?", "explain_architecture",
     {"topic": VISION}),
    (r"how do you (?:keep (?:me|this|us) safe|stay safe|protect (?:me|my (?:files|data)))",
     "explain_architecture", {"topic": SAFETY}),
    (r"(?:how (?:are you|do you stay) (?:safe|secure))", "explain_architecture", {"topic": SAFETY}),
    (r"how do you (?:think|reason|know what to do)", "explain_architecture", {"topic": REASONING}),
    (r"how do you (?:decide|choose)(?: what to do| what tool| which tool| a tool| your tools?)?",
     "explain_architecture", {"topic": REASONING}),
    (r"what (?:is|are) (?:lang ?graph|langraph)(?: for| about| doing here)?", "explain_architecture",
     {"topic": REASONING}),
    (r"how (?:do|does) (?:your|the) (?:reasoning|thinking|graph|loop) work", "explain_architecture",
     {"topic": REASONING}),
    (r"how do (?:your |the )?skills? work", "explain_architecture", {"topic": SKILLS}),
    (r"how (?:do you work|were you (?:built|made|designed)|does this work)", "explain_architecture",
     {"topic": OVERVIEW}),
    (r"(?:explain|describe) (?:your |the )?(?:architecture|design|internals|implementation)",
     "explain_architecture", {"topic": OVERVIEW}),
    (r"(?:explain|describe) how you work", "explain_architecture", {"topic": OVERVIEW}),
    (r"what (?:is|are) your (?:architecture|design|internals)", "explain_architecture",
     {"topic": OVERVIEW}),
    (r"how are you (?:built|made|put together|designed)", "explain_architecture", {"topic": OVERVIEW}),

    # --- who and what it is. Broadest, so last.
    (r"who (?:are|r) (?:you|u)(?: exactly| really)?", "core_identity", {}),
    (r"what (?:are|r) (?:you|u)", "core_identity", {}),
    (r"what(?: is|s)? your (?:name|purpose|job|role)", "core_identity", {}),
    (r"what (?:can|could) you do(?: for me| here| exactly)?", "core_identity", {}),
    (r"what (?:do|can) you do", "core_identity", {}),
    (r"what are your (?:abilities|capabilities|skills|tools|features|functions|powers)",
     "core_identity", {}),
    (r"what (?:tools?|skills?|abilities|capabilities|features) (?:do you have|have you got|are there)",
     "core_identity", {}),
    (r"(?:list|show) (?:me )?your (?:skills?|tools?|abilities|capabilities|features)",
     "core_identity", {}),
    (r"(?:tell me about|talk about|describe|introduce) yourself", "core_identity", {}),
    (r"tell me (?:who|what) you are", "core_identity", {}),
    (r"what are you (?:capable of|able to do|for)", "core_identity", {}),
]

COMPILED = [(re.compile(pattern), skill, params) for pattern, skill, params in RULES]


def strip_address(text: str) -> str:
    """Drop leading and trailing address and politeness words.

    Only from the ends, and only whole words. Removing them anywhere would turn
    "what can you tell me about the repo" into something that matches an
    identity pattern, which is the exact failure this module has to avoid.
    """
    words = normalise(text).split()
    while words and words[0] in LEAD_TRIM:
        words.pop(0)
    while words and words[-1] in TAIL_TRIM:
        words.pop()
    return " ".join(words)


def route(text: str, available=None) -> tuple[str, dict] | None:
    """(skill, params) when this is a question about FRIDAY itself, else None.

    `available` is the loaded registry. A rule naming a skill that is not
    loaded — disabled in settings, or dropped because its import failed —
    returns None rather than dispatching to something that is not there, and
    the ordinary reasoning loop gets the message instead. That keeps
    `skills.disabled` meaning what it says.
    """
    cleaned = strip_address(text)
    if not cleaned:
        return None
    for pattern, skill, params in COMPILED:
        if pattern.fullmatch(cleaned):
            if available is not None and skill not in available:
                return None
            return skill, dict(params)
    return None
