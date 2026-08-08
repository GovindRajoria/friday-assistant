# core/registry.py
"""Skill discovery and the JSON schema derived from the loaded manifests.

Discovery moves verbatim from the old core/main.py: same rglob + importlib +
setup() contract, same deliberate swallowing of import errors so a missing
dependency drops one skill rather than blocking boot (DESIGN.md:96-105).

What is no longer swallowed is the *evidence*. Dropping one skill instead of
refusing to boot is the right trade, but the old version printed a line to
stdout and kept nothing, so a skill that failed to load simply was not there —
no error, no event, nothing in the HUD. `scan_environment` fails exactly this
way when the exported OpenVINO model directory is missing: vision disappears
and the assistant cannot tell you it has gone blind. Failures now land in
`LOAD_FAILURES`, which the `skill_health` skill reads back on request.
"""
import importlib
import os
import traceback

from core.config import PROJECT_ROOT, SETTINGS

NO_ACTION = "none"

# Populated by discover_skills(), read by skills/utility/skill_health.py.
# Each entry: {"module", "phase", "error", "detail"}. `phase` separates "the
# import failed" from "setup() raised", because they have different fixes —
# a missing package versus missing data or hardware.
LOAD_FAILURES: list[dict] = []

# Skills deliberately switched off in settings, recorded so `skill_health` can
# distinguish "absent because it broke" from "absent because you said so".
SKIPPED_SKILLS: list[dict] = []

# Names of the skills that did load. Recorded here rather than counted from the
# live registry so `skill_health` can report it without a second discovery pass,
# which would re-import ultralytics and everything else for a status question.
LOADED_SKILLS: list[str] = []


def _disabled_names(settings: dict) -> set:
    return {str(name) for name in (settings.get("skills") or {}).get("disabled") or []}


def discover_skills(settings: dict | None = None) -> dict:
    """Import every module under skills/ and instantiate the ones exposing setup()."""
    settings = settings or SETTINGS
    disabled = _disabled_names(settings)

    active_skills = {}
    LOAD_FAILURES.clear()
    SKIPPED_SKILLS.clear()
    LOADED_SKILLS.clear()
    skills_dir = PROJECT_ROOT / "skills"

    for file_path in sorted(skills_dir.rglob("*.py")):
        if file_path.name == "__init__.py":
            continue

        # Convert absolute path to relative module name
        relative_path = file_path.relative_to(PROJECT_ROOT)
        module_name = str(relative_path.with_suffix("")).replace(os.sep, ".")

        # Checked against the module name *before* importing, as well as against
        # the manifest name after, so disabling a heavy skill also skips paying
        # for its imports — `annotate_image` pulls ultralytics and torch.
        if module_name in disabled or file_path.stem in disabled:
            SKIPPED_SKILLS.append({"module": module_name, "reason": "disabled in settings"})
            print(f"[.] Skipped (disabled): {module_name}")
            continue

        print(f"[*] Discovering: {module_name} at {file_path}")
        try:
            module = importlib.import_module(module_name)
        except Exception as error:
            LOAD_FAILURES.append({
                "module": module_name, "phase": "import",
                "error": f"{type(error).__name__}: {error}",
                "detail": traceback.format_exc(limit=3),
            })
            print(f"[-] Failed to import {module_name}: {error}")
            continue

        if not hasattr(module, "setup"):
            continue

        try:
            skill_instance = module.setup()
            skill_name = skill_instance.manifest["name"]
        except Exception as error:
            LOAD_FAILURES.append({
                "module": module_name, "phase": "setup",
                "error": f"{type(error).__name__}: {error}",
                "detail": traceback.format_exc(limit=3),
            })
            print(f"[-] Failed to construct {module_name}: {error}")
            continue

        if skill_name in disabled:
            SKIPPED_SKILLS.append({"module": module_name, "reason": "disabled in settings"})
            print(f"[.] Skipped (disabled): {skill_name}")
            continue

        active_skills[skill_name] = skill_instance
        LOADED_SKILLS.append(skill_name)
        print(f"[+] Loaded: {skill_name}")

    return active_skills


def build_action_schema(active_skills: dict) -> dict:
    """Derive the structured-output schema from the existing manifests.

    Constraining `action` to an enum of real skill names is what makes a
    hallucinated tool name impossible rather than merely unlikely.

    Every field is required. When `action` was optional, llama3.1 answered with
    a thought and no action at all — it narrated a plan and selected nothing.
    Requiring the field is what forces a commitment; `"none"` is how the model
    declines rather than by omission.

    A REJECTED FIX, RECORDED SO IT IS NOT TRIED AGAIN. `tools/routing_bench.py`
    showed the model answering correctly in its `thought` and then choosing a
    tool anyway: asked "how many bytes in a gigabyte" it thought *"A gigabyte is
    1,073,741,824 bytes"* and set `action` to `web_search`; asked what GPU stands
    for it answered "Graphics Processing Unit" and chose `translate`. 0/5 on the
    cases needing no tool.

    The obvious fix is to make the model commit to *whether* a tool is needed
    before choosing *which*, in a field of its own — structured output is
    generated in schema order, so a field placed before `action` is decided
    before it. Three variants were measured against the full case set:

      * `needs_tool: "yes"|"no"` after `thought` — fixed the arithmetic cases,
        then returned "no" for "restart the machine", "delete the temp folder"
        and "remind me to call the site". Reasonable, in fact: nothing needs
        looking up in order to restart a machine. The field had conflated needing
        to *read* something with needing to *do* something.
      * `intent: "do"|"look_up"|"answer"` after `thought`, naming that third
        case — returned "answer" for nearly everything, including "take a
        screenshot" and "what is the weather like", while the `action` field
        beside it named the right tool. Overall 56.2% -> 39.7%.
      * The same field moved *before* `thought`, testing whether the model was
        merely staying consistent with a narrative it had already begun — same
        result: "answer" for weather and screenshot.

    The reason is visible in the raw replies. The model writes the whole object
    as a script of a finished interaction — for `screenshot` it filled
    `final_answer` with "The screenshot has been saved to /tmp/screenshot.png"
    before anything ran, and for `weather` it wrote a forecast with
    `{temperature}` left as a literal placeholder. In that frame "answer" is not
    a classification of the request, it is a description of the story it is
    telling. Asking it to classify its own turn does not work, wherever the
    question is placed.

    What did work for the class of question where the model is confidently wrong
    was to stop asking it: see core/intents.py. What is still unfixed is the
    no-tool case, and it is honest to say so — the arithmetic and definition
    cases below still spend a wasted tool call before `conclude` answers from
    what it already knew.
    """
    return {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": (
                    "Your plan, spoken aloud before anything runs. One or two sentences saying what "
                    "you are about to do and why — never the answer itself, and never a result you "
                    "have not seen yet."
                ),
            },
            "action": {"type": "string", "enum": [*sorted(active_skills), NO_ACTION]},
            "action_input": {"type": "object"},
            "final_answer": {
                "type": "string",
                "description": "The answer to the user. Empty string unless action is 'none'.",
            },
        },
        "required": ["thought", "action", "action_input", "final_answer"],
    }
