# skills/utility/skill_health.py
"""Which skills failed to load, and why.

This closes a real invisible failure. `core/registry.py` deliberately swallows a
broken skill rather than refusing to boot — one missing package should not take
the whole assistant down — but for a long time that meant a failed skill left no
trace beyond a line on stdout that nobody reads. `scan_environment` dies exactly
this way when the exported OpenVINO model directory is missing: vision quietly
disappears, and asked to look at the room, FRIDAY says it has no such ability. It
is not lying, and it cannot tell you it has gone blind.

The registry now records every failure with the phase it happened in, and this
reads that back. The phase is the useful part, because it names the fix:

  import  — a package is missing. `pip install` in the venv.
  setup()  — the package is there and the *data* or hardware is not. An exported
             model directory, a camera, a config key.

Terminal: the report is the answer, and a model asked to summarise a list of
failures is a model with an opportunity to invent a reassuring one.
"""
from core import registry


class SkillHealthSkill:
    def __init__(self):
        self.manifest = {
            "name": "skill_health",
            "description": (
                "Reports which of your own skills failed to load and exactly why, which "
                "ones are switched off in settings, and how many loaded successfully. Use "
                "this when you cannot do something you believe you should be able to do, "
                "when an ability seems missing, or when asked whether all your skills are "
                "working. Its answer is complete — the turn ends when it returns. Use "
                "diagnose_self for the machine around you rather than your own skills."
            ),
            "parameters": [],
            "terminal": True,
        }

    def execute(self, params=None):
        failures = list(registry.LOAD_FAILURES)
        skipped = list(registry.SKIPPED_SKILLS)
        loaded = list(registry.LOADED_SKILLS)

        lines = [f"{len(loaded)} skill(s) loaded."]
        if not failures and not skipped:
            lines.append("Nothing failed to load and nothing is disabled.")
        else:
            if failures:
                by_phase = {"import": [], "setup": []}
                for failure in failures:
                    by_phase.setdefault(failure.get("phase", "import"), []).append(failure)

                if by_phase.get("import"):
                    lines.append(f"{len(by_phase['import'])} skill(s) could not be imported — "
                                 "a package is missing:")
                    for failure in by_phase["import"]:
                        lines.append(f"  {failure['module']}: {failure['error']}")
                if by_phase.get("setup"):
                    lines.append(f"{len(by_phase['setup'])} skill(s) imported but failed to "
                                 "start — the code is there, the data or hardware is not:")
                    for failure in by_phase["setup"]:
                        lines.append(f"  {failure['module']}: {failure['error']}")
            if skipped:
                names = ", ".join(sorted({s["module"] for s in skipped}))
                lines.append(f"{len(skipped)} skill(s) switched off in settings.skills.disabled: {names}")

        return {
            "status": "success",
            "message": "\n".join(lines),
            "data": {"loaded": len(loaded), "failures": len(failures),
                     "disabled": len(skipped),
                     "phases": sorted({f.get("phase") for f in failures})},
        }


def setup():
    return SkillHealthSkill()
