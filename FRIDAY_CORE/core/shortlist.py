# core/shortlist.py
"""Put ten skills in front of the model instead of forty-seven.

`tools/routing_bench.py` measured tool selection at 56% with forty-six skills
loaded, and the project has one clear data point about why: with nineteen skills
routing was visibly better, and the batch that took it to forty-six degraded it
badly enough that "Hello, friday" ran twenty tool calls. Every attempt since to
fix it by *telling* the model more — a schema field for whether a tool is needed,
sharper descriptions, negative clauses — has been measured and has either failed
or moved the problem somewhere else. The most recent of those is the clearest:
strengthening `media_control`'s description won its own three cases and then
started taking "minimise this window" and "kill the chrome process" from its
neighbours. Rewriting a description moves the attractor; it does not remove it.

What is left is the hypothesis those failures all point at: **it is attention, not
information.** Forty-seven descriptions is roughly eight thousand tokens of JSON
in the system prompt, and the right answer is frequently in there, correctly
worded, and not read. So this shrinks the list rather than improving it.

HOW IT SCORES. Lexical, because it has to be: `ollama list` on this machine is
`llama3.1` and `moondream`, with no embedding model, and pulling one to rank
forty-seven short documents would be a large dependency for a small job. Terms
are weighted by inverse document frequency across the manifests themselves, which
is what makes this work at all — "use", "this" and "the" appear in every
description and are worth nothing, while "volume", "clipboard" and "commit"
appear in one or two and are worth a great deal. A skill's own name counts for
more than its description, since a request that says "screenshot" is not being
subtle.

THE FAILURE THIS MUST NOT HAVE. A shortlist that omits the right tool is worse
than no shortlist, because the tool stops being *nameable*: the enum in
`core/registry.py` is built from the same subset, so a skill left out cannot be
chosen however obvious it is. Two rules guard that, and both matter more than the
ranking does:

  * **No confident match, no shortlist.** If nothing scores above `FLOOR` the
    whole registry is returned. A ranking built on noise is worse than none, and
    the honest signal for "I have no idea which of these it is" is a low score.
  * **The list is generous.** `DEFAULT_LIMIT` was not chosen by taste — it is the
    smallest size at which the labelled set in `tools/routing_cases.yaml` still
    contains the right answer for every case that has one, measured by
    `tests/test_shortlist.py`, which fails if that stops being true.
"""
import math
import re

# How many skills to show. Measured, not chosen: every case in
# tools/routing_cases.yaml that reaches the model keeps its correct answer inside
# the list at eight, and stays there at ten, twelve and fifteen — the curve is
# flat, so the smallest useful size wins on prompt tokens. Ten rather than the
# measured minimum of eight, because the case set was written before any of this
# existed and two spare slots are cheap insurance against the requests nobody
# thought to write down. tests/test_shortlist.py fails if that recall breaks.
DEFAULT_LIMIT = 10

# Below this best-score, the request has no meaningful overlap with any manifest
# and the ranking is noise. Falling back to the full registry there costs the
# prompt tokens this exists to save, on exactly the requests where guessing would
# make a tool unreachable.
FLOOR = 1.0

# A skill's own name is a much stronger signal than a word in its prose: somebody
# who says "screenshot" or "clipboard" has named the tool.
NAME_WEIGHT = 3.0

# Always offered, whatever the score. `web_search` is the universal fallback for
# anything factual, and a lookup phrased in words that appear in no manifest —
# which is most lookups, since manifests describe tools and not the world — would
# otherwise have nowhere to go.
ALWAYS_INCLUDE = frozenset({"web_search"})

# Words carrying no routing signal. Deliberately short: an over-eager stop list
# removes terms that turn out to matter ("open", "read", "run" are all verbs that
# genuinely distinguish skills), and IDF already handles the common ones by
# giving them a weight near zero.
STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "doing", "to", "of", "in", "on", "at", "for", "with",
    "and", "or", "but", "if", "then", "than", "this", "that", "these", "those",
    "it", "its", "you", "your", "yours", "i", "me", "my", "we", "our", "us",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    "not", "no", "so", "as", "by", "from", "about", "into", "over", "use",
    "used", "using", "when", "what", "which", "who", "how", "why", "please",
})

_WORD = re.compile(r"[a-z0-9_]+")


def terms(text: str) -> list[str]:
    """Lowercase word tokens, stopwords dropped, crudely singularised.

    The plural handling is deliberately small — two rules, not a stemmer, which
    would be a dependency and a source of surprises. But it does need both rules.
    Stripping a trailing "s" alone turns "processes" into "processe" while
    "process" stays "process", so the pair that motivated having any plural
    handling at all did not actually match; the "-es" case has to come first.
    """
    found = []
    for token in _WORD.findall((text or "").lower()):
        if token in STOPWORDS:
            continue
        found.append(_singular(token))
    return found


def _singular(token: str) -> str:
    # processes -> process, boxes -> box, watches -> watch
    if len(token) > 4 and token.endswith(("ses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    # files -> file, but not class -> clas
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def build_index(active_skills: dict) -> dict:
    """Per-skill term weights and the corpus IDF, computed once per call.

    Cheap enough not to cache: forty-seven short documents is a few hundred
    microseconds, and a cache keyed on the registry would be one more thing to
    invalidate when `skills.disabled` changes.
    """
    documents = {}
    for name, skill in active_skills.items():
        manifest = getattr(skill, "manifest", {}) or {}
        # The name counts as several occurrences rather than being merged into
        # the prose, so "screenshot" in a request outweighs a description that
        # merely mentions screenshots in passing.
        weights: dict[str, float] = {}
        for token in terms(name.replace("_", " ")):
            weights[token] = weights.get(token, 0.0) + NAME_WEIGHT
        for token in terms(str(manifest.get("description", ""))):
            weights[token] = weights.get(token, 0.0) + 1.0
        for parameter in manifest.get("parameters", []) or []:
            for token in terms(str(parameter).replace("_", " ")):
                weights[token] = weights.get(token, 0.0) + 0.5
        documents[name] = weights

    total = max(1, len(documents))
    frequency: dict[str, int] = {}
    for weights in documents.values():
        for token in weights:
            frequency[token] = frequency.get(token, 0) + 1
    # Smoothed, and floored at zero: a term in every manifest gets no weight at
    # all rather than a negative one, which would let a common word actively
    # push a skill down the list.
    idf = {token: max(0.0, math.log(total / count)) for token, count in frequency.items()}
    return {"documents": documents, "idf": idf}


def rank(utterance: str, active_skills: dict) -> list[tuple[str, float]]:
    """Every skill with its score, highest first. Ties broken by name.

    Sorted deterministically so the same request produces the same prompt twice —
    without that, a failure would be unreproducible and the benchmark would
    measure noise.
    """
    index = build_index(active_skills)
    wanted = set(terms(utterance))
    scored = []
    for name, weights in index["documents"].items():
        score = sum(weight * index["idf"].get(token, 0.0)
                    for token, weight in weights.items() if token in wanted)
        scored.append((name, round(score, 4)))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored


def shortlist(utterance: str, active_skills: dict, limit: int = DEFAULT_LIMIT) -> dict:
    """The subset of `active_skills` worth showing the model for this request.

    Returns the full registry unchanged when there is no confident match, when
    there are already few enough skills to show, or when the utterance is empty —
    each of which is a case where narrowing could only remove the right answer.
    """
    if not utterance or len(active_skills) <= limit:
        return active_skills

    scored = rank(utterance, active_skills)
    if not scored or scored[0][1] < FLOOR:
        return active_skills

    keep = {name for name, score in scored[:limit] if score > 0}
    keep |= {name for name in ALWAYS_INCLUDE if name in active_skills}
    return {name: skill for name, skill in active_skills.items() if name in keep}
