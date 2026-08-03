# core/profile.py
"""The operator's own biography, injected into the system prompt.

`config/settings.yaml` already holds a name, a location and an interests
string, but those are three short fields and a person is not three short
fields. This is free-form markdown the operator writes about themselves, and
it is what makes an answer land as "you know who I am" rather than as a
generic reply with a name pasted on the front.

Git-ignored, like settings.yaml, and for a stronger reason: this repository
is public and the file exists to hold personal detail.

Read fresh on every turn rather than once at import, because the HUD can edit
it. A cached copy would mean an edit did nothing until the backend was
restarted, which is exactly the kind of thing that gets diagnosed as "the
profile feature does not work". The read is a few kilobytes off local disk
once per turn; the model call it feeds takes several orders of magnitude
longer.
"""
from core.config import CONFIG_DIR

PROFILE_FILENAME = "about_me.md"
# A bound on what gets pasted into every system prompt. Someone who writes an
# essay should still get a working assistant rather than a context overflow.
MAX_PROFILE_CHARS = 8000


def profile_path():
    return CONFIG_DIR / PROFILE_FILENAME


def load_profile() -> str:
    """The operator's biography, or "" when they have not written one.

    Never raises. A missing or unreadable profile means the assistant is a
    little less personal, which is not a reason to fail a turn.
    """
    path = profile_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > MAX_PROFILE_CHARS:
        return text[:MAX_PROFILE_CHARS].rstrip() + "\n\n[profile truncated]"
    return text


def save_profile(text: str) -> None:
    """Write the biography, creating config/ if this is a fresh checkout."""
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
