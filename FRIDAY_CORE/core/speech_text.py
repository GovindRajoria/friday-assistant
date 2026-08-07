# core/speech_text.py
"""Rewrite an answer written for a screen into one meant for a voice.

The model is asked for prose and mostly produces it, but it is a chat model and
it reaches for markdown without being asked — bold for emphasis, backticks
around a filename, a bullet list when it is enumerating. On screen that reads
well. Aloud, `pyttsx3` says the punctuation: "star star ready star star", and a
URL becomes forty seconds of "h t t p colon slash slash". It is the single most
unprofessional thing this assistant does, and it does it in the mode where
nobody can see the text that would have explained it.

Two jobs, deliberately separate:

  * `for_speech` strips what a voice cannot render.
  * `sentences` cuts the result into units to enqueue one at a time.

Sentence-at-a-time enqueueing is not cosmetic. The speech engine is COM and
thread-affine (see server/app.py:_SpeechThread), so nothing outside its own
thread may call into it — which rules out `engine.stop()` and therefore rules
out interrupting an utterance already in progress. Handing it one sentence at a
time makes emptying the queue a real interruption, at a granularity of about a
second, using nothing but a queue. That is the whole reason this module returns
a list rather than a string.

Nothing here is lossy in a way that matters to meaning: markers are removed,
never the words inside them. The one exception is a fenced code block, which is
replaced by a phrase rather than read out — twenty lines of Python read aloud is
not information, it is noise with a shape.
"""
import re

# Fenced blocks first, before any inline rule can see inside one.
_FENCE = re.compile(r"```[^\n]*\n?(.*?)(?:```|\Z)", re.DOTALL)
# Bare URLs, and markdown links (whose text is worth keeping and whose target
# is not: "see the docs" says everything the operator can act on by ear).
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)
# Emphasis markers. Applied to the markers only, so the words survive.
_BOLD_OR_ITALIC = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")
# A list marker or heading at the start of a line. The line's content stays; a
# spoken "dash" or "hash hash" in front of every item does not.
_LINE_MARKER = re.compile(r"^[ \t]*(?:[-*+]|#{1,6}|>)[ \t]+", re.MULTILINE)
# "1. " and "1) " likewise — but only at a line start, so "3.5 GB" and a
# sentence ending in a year are untouched.
_ORDERED_MARKER = re.compile(r"^[ \t]*\d{1,2}[.)][ \t]+", re.MULTILINE)
_TABLE_PIPES = re.compile(r"[ \t]*\|[ \t]*")
_HORIZONTAL_RULE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.MULTILINE)
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{2,}")
# Where an inline-code span was lifted out to. NUL cannot appear in text the
# model produced, so there is nothing to collide with.
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def for_speech(text: str) -> str:
    """The same answer with everything a voice cannot pronounce taken out."""
    if not text:
        return ""

    cleaned = _FENCE.sub(lambda match: _describe_code(match.group(1)), text)

    # Inline code is lifted out before anything else runs and put back at the
    # end, because its contents are the one place in an answer where markdown
    # punctuation is not markdown. `src/**/*.ts` is a real path an operator
    # wants to hear, and every rule below would happily mangle it: the emphasis
    # rule pairs the asterisks up across it and reads back `src/*/.ts`, which is
    # a plausible-sounding path that does not exist. That is worse than saying
    # "star" — a wrong answer instead of an ugly one.
    stashed: list[str] = []

    def stash(match: "re.Match[str]") -> str:
        stashed.append(match.group(1))
        return f"\x00{len(stashed) - 1}\x00"

    cleaned = _INLINE_CODE.sub(stash, cleaned)
    cleaned = _MARKDOWN_LINK.sub(r"\1", cleaned)
    cleaned = _URL.sub("a link", cleaned)
    cleaned = _HORIZONTAL_RULE.sub("", cleaned)
    cleaned = _LINE_MARKER.sub("", cleaned)
    cleaned = _ORDERED_MARKER.sub("", cleaned)
    # Twice, so `**a *b* c**` loses both layers. Bounded rather than looped —
    # two is every case that occurs in practice and a loop here is a way to
    # spend an unbounded amount of time on an adversarial answer.
    cleaned = _BOLD_OR_ITALIC.sub(r"\2", cleaned)
    cleaned = _BOLD_OR_ITALIC.sub(r"\2", cleaned)
    cleaned = _TABLE_PIPES.sub(" ", cleaned)
    cleaned = _MULTI_SPACE.sub(" ", cleaned)
    cleaned = _MULTI_NEWLINE.sub("\n", cleaned)
    cleaned = _PLACEHOLDER.sub(lambda match: stashed[int(match.group(1))], cleaned)
    # Line by line, not just the ends: a table row loses its leading pipe and is
    # left starting with a space, and `sentences` would carry that into the
    # queue.
    return "\n".join(line.strip() for line in cleaned.strip().splitlines())


def _describe_code(body: str) -> str:
    """What to say instead of reading a code block out.

    The line count is included because it is the one thing about the block that
    is useful by ear: it tells the operator whether to go and look at the screen.
    """
    lines = [line for line in body.strip().splitlines() if line.strip()]
    if not lines:
        return "an empty code block."
    if len(lines) == 1:
        return f"{lines[0].strip()}."
    return f"a {len(lines)}-line code block, on screen."


# A period after one of these is an abbreviation, not the end of a sentence.
#
# Only words that are genuinely followed by a capital *mid-sentence* belong here,
# which is a much shorter list than "abbreviations". A boundary is only even
# considered when the next character is upper case (see _SENTENCE_END), so
# anything normally followed by a lower-case word — "at 4 p.m. tomorrow" — is
# already safe and adding it would only suppress real boundaries. "etc." and
# "p.m." were in this set initially and came out for exactly that: followed by a
# capital they almost always are ending a sentence, and blocking the split
# glued two sentences into one unbroken lump of speech.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs",
    "e.g", "i.e", "approx", "no", "fig", "al", "inc", "ltd",
}
# End of sentence: closing punctuation, then whitespace, then something that
# starts a new one. Requiring the next character to be upper case or a quote is
# what keeps "version 3. 5" — which the model does write — in one piece.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def sentences(text: str) -> list[str]:
    """Cut speech-ready text into what to enqueue, one item at a time.

    Newlines split too, not only punctuation: a list item is a unit somebody
    would pause between, and the model frequently writes items with no full stop
    at the end of them at all — split on punctuation alone, a six-item list is
    one six-second blob that cannot be interrupted in the middle.
    """
    if not text:
        return []
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts.extend(_split_line(line))
    return parts


def _split_line(line: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(line):
        candidate = line[start:match.start()].strip()
        if _ends_on_an_abbreviation(candidate):
            continue                       # keep going; this was not the end
        if candidate:
            pieces.append(candidate)
        start = match.end()
    tail = line[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def _ends_on_an_abbreviation(candidate: str) -> bool:
    if not candidate.endswith("."):
        return False
    last = candidate[:-1].rsplit(" ", 1)[-1].lower()
    # A single letter is an initial ("J. Smith"), which is the same case.
    return last in _ABBREVIATIONS or len(last) == 1
