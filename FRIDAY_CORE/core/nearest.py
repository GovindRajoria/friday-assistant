# core/nearest.py
"""The closest thing to what somebody said, offered as a question.

Several skills take a name out of a closed set — a timezone, an application, a
settings key — and a name that matches nothing produces a dead end. That happens
constantly here for one reason: **speech recognition mishears proper nouns.**
Asked for the time in Tokyo, `small.en` returned "Dukyo", and the reply to a
perfectly clear question was a sentence about timezone naming conventions.

**Suggest, never substitute. This is the whole point of the module and it is a
measured position, not a cautious one.** Fuzzy matching a misheard name onto the
closed set is the obvious repair and it cannot be made safe here. Scored against
the real timezone database with difflib:

    dukyo   -> tokyo      0.60   (tied with "yukon", also 0.60)
    tokio   -> tokyo      0.80
    londen  -> london     0.83

    atlantis -> atlantic  0.88
    gotham   -> godthab   0.77
    asgard   -> kashgar   0.77
    narnia   -> manila    0.67

The mishearings that matter sit *below* the fantasies. There is no cutoff that
accepts "Dukyo" and refuses "Atlantis", so any threshold low enough to be useful
will also answer a question about Narnia with the time in Manila — confidently,
in a full sentence, with no sign anything was guessed. That is the failure this
project treats as worse than an error message.

Asking costs nothing by comparison. "Did you mean Tokyo?" is right when the guess
is right, obviously wrong when it is not, and with the follow-up window open the
answer to it is one word.

**The suggestion is often not the right one, and that is not a defect to fix.**
"dukyo" scores 0.60 against "tokyo" and 0.60 against "yukon" — an exact tie — so
the nearest match to the case this module was built for is a coin flip. Two ideas
for breaking that were tried and measured. Comparing consonant skeletons, which
sounds principled, is much worse: with vowels stripped "tokio" matches "atka"
perfectly and "bhopaal" loses to "palau", because five-letter words have almost
no signal left. Preferring similar lengths does nothing, since the confusable
names are all the same length. String distance simply cannot tell Tokyo from
Yukon, and no amount of cleverness at this layer will change that — which is one
more reason the output must be a question and not an answer.

WHERE THIS IS DELIBERATELY NOT USED. Filenames and paths: `manage_files` deletes
things, and a suggestion that names a real file the operator did not mean is a
worse prompt to say yes to than a plain failure. Process names: the confirmation
gate already shows the exact call to a human before anything is killed, so the
mishearing is visible where it matters.
"""
import difflib

# Below this, the nearest match is noise and offering it reads as flailing. 0.6
# is where the measured mishearings sit — see the table above. It is deliberately
# a suggestion threshold and would be far too low to act on.
SUGGEST_RATIO = 0.6


def nearest(wanted: str, options, ratio: float = SUGGEST_RATIO) -> "str | None":
    """The closest option to `wanted`, or None when nothing is close enough.

    `options` is anything iterable of strings — the keys of a map, a list of
    names. Comparison is case-insensitive; the option is returned as it was
    given, so the caller can show it the way it is spelled.
    """
    if not wanted:
        return None
    lowered = wanted.strip().lower()
    if not lowered:
        return None

    # Sorted, so a tie resolves the same way every time. Ties are not rare at
    # this threshold and the alternative is a suggestion that depends on set
    # iteration order — a different answer to the same question between runs,
    # which is far more confusing than a suggestion that is merely wrong.
    best, score = None, 0.0
    for option in sorted(options, key=str):
        candidate = difflib.SequenceMatcher(None, lowered, str(option).lower()).ratio()
        if candidate > score:
            best, score = option, candidate
    return best if score >= ratio else None


def did_you_mean(wanted: str, options, ratio: float = SUGGEST_RATIO) -> str:
    """" Did you mean X?", or "" when nothing is close.

    A sentence fragment rather than a whole message, so each caller keeps its own
    wording for what actually failed — and returns "" rather than None so it can
    be concatenated without a branch at every call site.
    """
    match = nearest(wanted, options, ratio)
    return f" Did you mean {match}?" if match else ""
