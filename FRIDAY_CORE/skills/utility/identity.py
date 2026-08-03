# skills/utility/identity.py
"""Who FRIDAY is and what it can do.

Marked terminal in its manifest, which core/graph.py enforces: this skill's
answer IS the answer, so the turn ends the moment it returns.

That is not a nicety. Asked "what are your abilities?", the model called this
skill, got a complete reply, and then kept going for eleven more steps —
drafting two documents, writing two memory entries, taking a webcam photo
which tripped the privacy guard and muted the machine's audio, and fetching
unrelated web pages, before finally hitting the step bound and giving up.
A skill whose output is the whole answer must be able to say so in a way the
graph honours rather than the prompt requests.

The capability list is built from the loaded skills rather than written out
by hand. The hardcoded sentence this replaces named "web, OS control,
applications, diagnostics and documents" and had drifted: it never mentioned
the vision, news, weather, clipboard or file skills at all.
"""


class CoreIdentitySkill:
    def __init__(self):
        self.manifest = {
            "name": "core_identity",
            "description": (
                "Use this when the user asks who you are, what you can do, what your "
                "skills are, or what your purpose is. Its answer is complete — the turn "
                "ends when it returns. DO NOT use this for web searches."
            ),
            "parameters": [],
            "terminal": True,
        }

    def execute(self, params=None):
        # Imported here rather than at module scope: core.registry imports this
        # module while it is discovering skills, and importing the registry back
        # at that point is a cycle.
        from core.registry import discover_skills

        try:
            names = sorted(discover_skills())
        except Exception:  # noqa: BLE001 — a broken registry must not break "who are you"
            names = []

        # core_identity itself is not a capability worth announcing.
        names = [name for name in names if name != "core_identity"]
        listed = ", ".join(names) if names else "no skills are currently loaded"
        return {
            "status": "success",
            "message": (
                "I am FRIDAY, a local-first assistant. Everything I do runs on this "
                f"machine. I currently have {len(names)} skills loaded: {listed}. "
                "Ask me for any of that in plain language."
            ),
        }


def setup():
    return CoreIdentitySkill()
