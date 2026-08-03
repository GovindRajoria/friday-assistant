"""How FRIDAY works, answered from a file rather than from memory.

Asked how it is built, a language model will produce a confident and entirely
generic description of some assistant it read about during training. It will
be plausible, it will be wrong in the specifics, and nothing in the reply
signals which parts are which. That is the same failure as inventing
headlines, and it gets the same fix: the answer comes from a document, and
the document lives beside the code.

`docs/ARCHITECTURE.md` is that document. Its `##` headings are the topics
this skill accepts, so adding a section there adds a topic here with no code
change. One section is returned verbatim — the model is not asked to
summarise it, because a summary is exactly where an invented detail would
reappear.

Marked terminal, as `core_identity` is and for the same reason: the section
is the whole answer, and a skill whose output is the answer has to be able to
say so in a way the graph honours rather than the prompt requests.
"""
from core.config import PROJECT_ROOT

# docs/ sits at the repository root, one level above FRIDAY_CORE. The second
# candidate covers a deployment that ships the backend directory alone.
CANDIDATE_PATHS = (
    PROJECT_ROOT.parent / "docs" / "ARCHITECTURE.md",
    PROJECT_ROOT / "docs" / "ARCHITECTURE.md",
)

# What people actually say, mapped to the section that answers it. The model
# picks the `topic` value, and it will not reliably pick the exact slug — asked
# "what is LangGraph for", it says "langgraph". A miss falls back to the
# overview rather than to an error, so a wrong guess still answers the
# question approximately instead of not at all.
ALIASES = {
    "langgraph": "reasoning",
    "graph": "reasoning",
    "loop": "reasoning",
    "brain": "reasoning",
    "thinking": "reasoning",
    "state machine": "reasoning",
    "tools": "skills",
    "abilities": "skills",
    "capabilities": "skills",
    "speech": "voice",
    "microphone": "voice",
    "audio": "voice",
    "stt": "voice",
    "whisper": "voice",
    "listening": "voice",
    "camera": "vision",
    "screen": "vision",
    "webcam": "vision",
    "eyes": "vision",
    "security": "safety",
    "privacy": "safety",
    "confirmation": "safety",
    "permissions": "safety",
    "reminders": "proactive",
    "briefing": "proactive",
    "scheduler": "proactive",
    "ui": "interface",
    "hud": "interface",
    "electron": "interface",
    "window": "interface",
    "websocket": "interface",
    "server": "interface",
    "config": "configuration",
    "settings": "configuration",
    "offload": "configuration",
    "architecture": "overview",
    "everything": "overview",
    "design": "overview",
}


def parse_sections(text: str) -> dict:
    """Map each `## ` heading to (title, body).

    The slug is the heading's first word, lowercased — "Reasoning loop"
    becomes "reasoning" — which keeps the topics short enough for the model to
    hit without the headings having to be single words for humans.
    """
    sections, title, body = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if title is not None:
                sections[title.split()[0].lower()] = (title, "\n".join(body).strip())
            title, body = line[3:].strip(), []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections[title.split()[0].lower()] = (title, "\n".join(body).strip())
    return sections


def resolve(topic: str, sections: dict) -> str:
    """Which section answers this topic. Falls back to the overview."""
    asked = (topic or "").strip().lower()
    if asked in sections:
        return asked
    if asked in ALIASES and ALIASES[asked] in sections:
        return ALIASES[asked]
    # Last resort before the overview: a topic that contains a known word,
    # which covers the model answering "the reasoning loop" instead of
    # "reasoning".
    for word in asked.replace("-", " ").split():
        if word in sections:
            return word
        if word in ALIASES and ALIASES[word] in sections:
            return ALIASES[word]
    return "overview" if "overview" in sections else next(iter(sections), "")


class ExplainArchitectureSkill:
    def __init__(self):
        self.manifest = {
            "name": "explain_architecture",
            "description": (
                "Use this when the user asks HOW you work, how you are built, how you "
                "decide what to do, what LangGraph does here, or how any part of you "
                "works internally. Topics: overview, reasoning, skills, voice, vision, "
                "safety, proactive, interface, configuration. Its answer is complete — "
                "the turn ends when it returns. Use core_identity instead for WHAT you "
                "can do, and do not use this for anything about the outside world."
            ),
            "parameters": ["topic"],
            "terminal": True,
        }

    def _read(self) -> str:
        for path in CANDIDATE_PATHS:
            if path.exists():
                return path.read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"docs/ARCHITECTURE.md was not found (looked in {', '.join(str(p) for p in CANDIDATE_PATHS)})"
        )

    def execute(self, params=None):
        try:
            sections = parse_sections(self._read())
        except OSError as error:
            # Honest about the gap rather than answering from the model's own
            # idea of how assistants work, which is the entire point of this
            # skill existing.
            return {
                "status": "error",
                "message": f"I could not read my own architecture notes, so I will not guess: {error}",
            }

        if not sections:
            return {"status": "error", "message": "My architecture notes are present but have no sections in them."}

        key = resolve((params or {}).get("topic", ""), sections)
        title, body = sections[key]
        others = ", ".join(sorted(name for name in sections if name != key))
        return {
            "status": "success",
            "message": f"{title}.\n\n{body}\n\nI can also explain: {others}.",
        }


def setup():
    return ExplainArchitectureSkill()
