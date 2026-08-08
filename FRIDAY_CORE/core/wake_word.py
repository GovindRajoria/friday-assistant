# core/wake_word.py
"""Did this utterance address the assistant, and what did it actually ask?

The console listener (`core/listener.py`) has always done a plain
`if self.wake_word in transcription`. That is fine when a human deliberately
started the recording. Continuous listening is a harder problem in both
directions, and this module exists because a substring test fails both ways:

**False negatives.** Whisper does not reliably render a proper noun as one word.
"Friday" comes back as "Friday", "Fry day", "Fri day", "Freeday", "Friyay" and
worse on an accented voice, and `small.en` is an English-only model being asked
for an Indian English speaker's pronunciation of a name. A substring test misses
every one of those and the assistant appears deaf.

**False positives.** "I'll do it on Friday" is a sentence about a day of the week,
not an address to an assistant. With auto-submit enabled, that becomes a turn
nobody asked for. So the wake word has to appear at the *start* of what was said —
which is where a person puts it when they are addressing someone — rather than
anywhere in the sentence.

Both bounds are deliberately loose in the safe direction: a missed wake word costs
one repetition, while a false trigger runs a turn on a room conversation.
"""
import difflib
import re

# How many leading words may be searched for the wake word. Three covers "hey
# friday", "ok friday" and a stray filler; beyond that the word is being used in
# a sentence rather than to address anyone.
LEAD_WORDS = 3
# Similarity above which a token counts as the wake word. 0.75 accepts "friyay"
# and "frydey" while rejecting "priority" and "birthday".
FUZZY_THRESHOLD = 0.75

# Mishearings seen or plainly likely for "friday", and applied ONLY when
# "friday" is the configured wake word — a site that renames the assistant to
# "computer" must not inherit these. Checked as joined pairs too, because Whisper
# often splits a name into two tokens.
#
# "thursday" is deliberately NOT here despite being a plausible mishearing. The
# asymmetry that governs this whole module says why: a missed wake word costs one
# repetition, while "Thursday, are we still on?" said to another person in the
# room would run a turn. A real, different, commonly-spoken word is too expensive
# to accept.
DEFAULT_WAKE_WORD = "friday"
KNOWN_VARIANTS = {
    "friday", "fridays", "fryday", "fridae", "freeday", "friyay", "frydey",
    "fridy", "friday's", "phriday", "frida", "freda", "friddy",
}
# Politeness that may precede the name and should not defeat the position test.
LEADING_FILLER = {"hey", "hi", "hello", "ok", "okay", "yo", "um", "uh", "so", "and", "please"}

_PUNCTUATION = re.compile(r"[^\w\s']+")


def _tokens(text: str) -> list[str]:
    return _PUNCTUATION.sub(" ", (text or "").lower()).split()


def _matches(token: str, wake_word: str) -> bool:
    if token == wake_word:
        return True
    # The hand-written mishearings belong to the default name only.
    if wake_word == DEFAULT_WAKE_WORD and token in KNOWN_VARIANTS:
        return True
    # difflib rather than a phonetic library: no new dependency, and the
    # mishearings that matter here are spelling-adjacent rather than
    # phonetically distant.
    return difflib.SequenceMatcher(None, token, wake_word).ratio() >= FUZZY_THRESHOLD


def find(text: str, wake_word: str = "friday") -> tuple[bool, str]:
    """(addressed, command).

    `addressed` is True when the utterance opens by naming the assistant.
    `command` is what remains once the name and any leading filler are removed —
    empty when the operator said only the name, which the caller should treat as
    "they want my attention", not as an empty request.
    """
    wake_word = (wake_word or "friday").lower().strip()
    tokens = _tokens(text)
    if not tokens:
        return False, ""

    for index, token in enumerate(tokens[:LEAD_WORDS]):
        # A word before the name is only allowed to be filler. This is what
        # stops "I'll call you Friday" matching on its third token.
        if index and tokens[index - 1] not in LEADING_FILLER and not _matches(tokens[index - 1], wake_word):
            break

        if _matches(token, wake_word):
            return True, " ".join(tokens[index + 1:]).strip()

        # Whisper splitting the name in two: "fri day", "free day".
        if index + 1 < len(tokens) and _matches(token + tokens[index + 1], wake_word):
            return True, " ".join(tokens[index + 2:]).strip()

    return False, ""


# Said to stop talking. Matched against the WHOLE utterance and never as a
# prefix, which is the only thing keeping "stop the build" and "cancel the
# deployment" — real requests, and destructive ones — from being swallowed as
# commands to be quiet instead of carried out.
#
# The exactness also does most of the work against hearing itself. An answer is
# spoken one sentence at a time and the microphone segments on silence, so a
# sentence of the assistant's own speech can arrive here as a complete utterance;
# what saves it is that "Stop." is not a sentence this assistant says. "I have
# stopped the service" is, and it does not match.
STOP_PHRASES = frozenset({
    "stop", "stop it", "stop talking", "stop please",
    "be quiet", "quiet", "quiet please", "shush", "hush",
    "shut up", "enough", "thats enough", "that is enough",
    "never mind", "nevermind", "forget it", "cancel", "cancel that",
})


def is_stop_command(text: str, wake_word: str = "friday") -> bool:
    """Was that "be quiet", rather than something to do?

    The name is optional and stripped either way: hands-free, "friday stop" and
    "stop" are the same instruction, and requiring the name from someone trying to
    interrupt would be the worst possible moment to insist on protocol.
    """
    wake_word = (wake_word or DEFAULT_WAKE_WORD).lower().strip()
    tokens = _tokens(text)
    while tokens and (tokens[0] in LEADING_FILLER or _matches(tokens[0], wake_word)):
        tokens.pop(0)
    return " ".join(tokens) in STOP_PHRASES
