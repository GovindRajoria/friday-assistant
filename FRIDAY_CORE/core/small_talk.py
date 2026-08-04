# core/small_talk.py
"""Is this whole message just conversation? Decided in Python, not by the model.

The system prompt has told the model since Phase 1 that a greeting needs no tool
(OPERATING RULES 2) and that `core_identity` is only for capability questions
(rule 4). Observed live on 2026-08-04, with 45 skills loaded, in answer to
"Hello, friday.":

    describe_screen -> describe_screen (refused as a repeat) -> read_webpage
    -> read_webpage (refused) -> web_search -> web_search -> web_search
    -> core_identity -> answered with a list of all 44 tools

Twenty-odd steps, a webcam-adjacent screen grab, three web searches, and a reply
that was a tool inventory. Every one of those steps violated a written rule. This
is the same lesson as the anomaly rule and the `terminal` flag: when asking has
failed repeatedly, the graph has to make the wrong move unavailable rather than
discouraged. More tools made it worse — 45 plausible actions is 45 ways to avoid
simply saying hello.

The rule is deliberately narrow, because the cost of a false positive is real: a
message classified as small talk cannot reach a tool at all, so "hi, what's the
weather" must never match. Every token has to be conversational. One substantive
word anywhere and this returns False and the ordinary reasoning loop runs.
"""
import re

# Fillers that carry no request on their own. A message made only of these, in
# any order and any quantity, is conversation.
GREETING_WORDS = {
    "hello", "hi", "hey", "heya", "hiya", "yo", "greetings", "howdy",
    "morning", "afternoon", "evening", "night", "goodnight", "goodbye", "bye",
    "cheers", "thanks", "thank", "thankyou", "ta", "ok", "okay", "kk", "k",
    "cool", "nice", "great", "good", "fine", "alright", "right", "sure",
    "yes", "yeah", "yep", "no", "nope", "nah", "please", "sorry", "excuse",
    "welcome", "sup", "wassup", "hola", "namaste", "salaam", "salam",
    "there", "again", "friday", "you", "u", "me", "my", "friend", "mate",
    "sir", "madam", "buddy", "pal", "all", "very", "much", "lot", "lots",
    "well", "so", "and", "a", "the", "is", "it", "im", "i", "am", "are",
    "how", "doing", "today", "up", "whats", "what", "s", "your", "day",
    "was", "hope", "just", "wanted", "say", "checking", "in", "still", "here",
}

# Phrases that are conversation even though their words look like a question.
# Matched against the whole normalised message, so "how are you doing today"
# hits and "how are the tests doing" does not.
CONVERSATIONAL_PHRASES = {
    "how are you", "how are you doing", "how are you today", "how are things",
    "how is it going", "hows it going", "how do you do", "you there",
    "are you there", "you awake", "are you awake", "you alive", "are you alive",
    "good morning", "good afternoon", "good evening", "good night",
    "thank you", "thanks a lot", "thanks very much", "thank you very much",
    "nice to meet you", "long time no see", "whats up", "what is up",
}

# Any of these means there is a real request in the message no matter what else
# is in it. Checked before the token test, because "hi, what's the weather" and
# "thanks, now delete that file" must both reach the reasoning loop.
SUBSTANTIVE_MARKERS = (
    "weather", "news", "search", "find", "open", "read", "write", "delete",
    "remind", "remember", "calendar", "email", "task", "file", "run", "test",
    "screen", "camera", "translate", "price", "disk", "gpu", "network",
    "process", "lock", "shut", "restart", "commit", "branch", "repo", "code",
    "document", "spreadsheet", "note", "journal", "settings", "volume", "mute",
    "who are you", "what can you do", "abilities", "capabilities", "skills",
    "how do you work", "architecture", "last turn", "diagnose", "health",
)

_NORMALISE = re.compile(r"[^a-z0-9\s]+")
_MAX_WORDS = 12


def normalise(text: str) -> str:
    """Lowercased, punctuation stripped, whitespace collapsed."""
    return " ".join(_NORMALISE.sub(" ", (text or "").lower()).split())


def is_small_talk(text: str) -> bool:
    """True when the entire message is conversation and needs no tool."""
    cleaned = normalise(text)
    if not cleaned:
        return False

    # A long message is doing something, whatever words it uses.
    words = cleaned.split()
    if len(words) > _MAX_WORDS:
        return False

    # One real noun or verb anywhere disqualifies the whole message.
    if any(marker in cleaned for marker in SUBSTANTIVE_MARKERS):
        return False

    if cleaned in CONVERSATIONAL_PHRASES:
        return True

    return all(word in GREETING_WORDS for word in words)
