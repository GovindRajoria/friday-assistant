# skills/vision/describe_screen.py
"""Look at the screen right now, on request.

Distinct from the background watcher in vision/watcher.py, and both exist for
a reason. The watcher runs on its own thread whether or not anyone asked,
gated on the screen having changed, and its description is ambient context
folded into the next question — it is deliberately allowed to be stale, and
it is off by default. This skill is the opposite: it captures at the moment
of asking and blocks until it has an answer, because "what is on my screen?"
is a question about now.

It also works with `screen.enabled: false`. Someone who does not want a
process watching their desktop continuously may still want to ask about it
once, and refusing that because a background setting is off would be
answering a different question than the one asked.

mss and PIL are imported inside execute(), not at module scope: neither is
installed in CI, and tests/test_imports_without_runtime_deps.py imports this
package. The same reason core/llm_client.py imports ollama lazily.
"""
from core.config import SETTINGS

# The watcher's own measurements apply: 1280px described in ~0.19s median and
# did not hallucinate, where 768px was no faster and invented a program that
# was not running. Do not shrink this without re-checking output quality.
CAPTURE_WIDTH = 1280

# One retry. A describe pass costs ~0.19s, so a second attempt is cheap
# relative to handing the model a token to reason about.
ATTEMPTS = 2


def looks_like_a_description(text: str) -> bool:
    """Reject the identifier-shaped junk moondream sometimes emits.

    Observed twice in this project: `urn:1f6c8b0` while the image encoding
    was wrong, and `urn:ietf:wg:ac:200` from a perfectly good PNG. It is not
    an encoding bug — the model simply produces a token instead of a sentence
    sometimes, the same way it returns an empty string sometimes. Passing one
    through means the assistant confidently reports a URN as the contents of
    the screen, which is what happened before this guard existed.

    Deliberately crude: a real description is several words of prose. Anything
    shorter, or shaped like a scheme:identifier, is not one.
    """
    stripped = (text or "").strip()
    if len(stripped.split()) < 4:
        return False
    # A leading "scheme:" with no spaces before it is the shape of an
    # identifier, never of English.
    head = stripped.split(maxsplit=1)[0]
    return not (":" in head and " " not in head)


class DescribeScreenSkill:
    def __init__(self):
        self.manifest = {
            "name": "describe_screen",
            "description": (
                "Captures the screen right now and describes what is on it. Use this "
                "when asked what is on screen, what the operator is looking at, or to "
                "read something they are pointing at. This is a fresh capture, not the "
                "ambient background description. Parameter: 'monitor' (optional, "
                "defaults to the primary display)."
            ),
            "parameters": ["monitor"],
        }

    def execute(self, params=None):
        params = params or {}
        try:
            monitor = int(params.get("monitor") or SETTINGS["screen"]["monitor"])
        except (TypeError, ValueError):
            monitor = SETTINGS["screen"]["monitor"]

        try:
            from vision.capture import grab_png
            from vision.describers import get_describer
        except ImportError as error:
            # mss/Pillow absent. An error observation is the right surface —
            # the graph turns it into something the model can say out loud.
            return {"status": "error", "message": f"Screen capture is not available here: {error}"}

        try:
            png = grab_png(monitor, CAPTURE_WIDTH)
        except Exception as error:  # noqa: BLE001 — a capture failure must read as an observation, not a traceback
            return {"status": "error", "message": f"I could not capture the screen: {error}"}

        describer = get_describer()
        rejected = None
        for _ in range(ATTEMPTS):
            try:
                description = (describer.describe(png) or "").strip()
            except Exception as error:  # noqa: BLE001 — an unreachable vlm.host is ordinary, not exotic
                return {"status": "error", "message": f"I could not describe the screen: {error}"}

            if looks_like_a_description(description):
                return {
                    "status": "success",
                    "message": f"On screen right now: {description}",
                    "data": {"monitor": monitor},
                }
            rejected = description

        # Reported rather than passed on. Handing the model `urn:ietf:wg:ac:200`
        # gets it repeated to the operator as though it were the screen's
        # contents; saying the read failed is the honest outcome.
        detail = f" It returned {rejected!r}." if rejected else ""
        return {"status": "error",
                "message": f"The vision model did not produce a usable description.{detail}"}


def setup():
    return DescribeScreenSkill()
