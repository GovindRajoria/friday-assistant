# core/briefing.py
"""The morning briefing, composed in Python rather than by an agent turn.

This is the one piece of the assistant that runs with nobody watching, and
that is exactly why it does not go through the graph. A free reasoning turn
decides for itself which tools to call, and this project has already watched
that go wrong twice in one day: a capabilities question that drafted two
documents, wrote two memory entries and took a webcam photo, and a news
request answered with invented headlines. Supervised, those are irritating.
Unsupervised at 08:00 they are unacceptable.

So the briefing calls a fixed, read-only set of skills directly, and spends
exactly one model call turning what they returned into prose. It cannot
choose a different tool, it cannot write a file, and it cannot delete
anything, because it is never asked what to do.

If the phrasing call fails, the raw sections are returned instead. A briefing
in plain bullet points is still a briefing; silence is not.
"""
from core import llm_client
from core.config import SETTINGS

# Read-only, every one of them. Adding to this list is the only way to widen
# what an unattended briefing can touch — do not add anything with a side
# effect, and note that none of these carry `destructive` in their manifest.
BRIEFING_SKILLS = ("weather", "read_news")

MAX_HEADLINES = 6


def gather_sections(active_skills: dict) -> list[tuple[str, str]]:
    """Run the briefing skills and return (label, text) for each that worked.

    A skill that is missing or fails is skipped rather than aborting the
    briefing — no weather is a thinner briefing, not a reason to say nothing.
    """
    sections = []
    for name in BRIEFING_SKILLS:
        skill = active_skills.get(name)
        if skill is None:
            continue
        params = {"count": MAX_HEADLINES} if name == "read_news" else {}
        try:
            result = skill.execute(params)
        except Exception as error:  # noqa: BLE001 — one dead skill must not cancel the briefing
            result = {"status": "error", "message": str(error)}
        if result.get("status") == "success" and result.get("message"):
            sections.append((name, result["message"]))
    return sections


def compose(active_skills: dict, address_as: str | None = None) -> str:
    """Return the briefing text, or "" when there was nothing to say."""
    sections = gather_sections(active_skills)
    if not sections:
        return ""

    address_as = address_as or SETTINGS["assistant"]["address_user_as"]
    raw = "\n\n".join(f"[{label}]\n{text}" for label, text in sections)

    prompt = (
        "You are writing a short spoken morning briefing.\n"
        f"Address the listener as '{address_as}'. Two or three sentences, no more.\n"
        "Use ONLY the material below. Do not add any fact, headline, figure or "
        "forecast that is not in it — this is read out unattended and nobody is "
        "there to catch an invention.\n"
        "Lead with the weather, then the two or three most significant stories.\n\n"
        f"{raw}"
    )
    try:
        spoken = llm_client.chat([{"role": "user", "content": prompt}]).strip()
    except Exception:  # noqa: BLE001 — a briefing in bullet points beats no briefing
        return raw
    return spoken or raw
