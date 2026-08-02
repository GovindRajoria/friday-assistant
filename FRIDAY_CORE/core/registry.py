# core/registry.py
"""Skill discovery and the JSON schema derived from the loaded manifests.

Discovery moves verbatim from the old core/main.py: same rglob + importlib +
setup() contract, same deliberate swallowing of import errors so a missing
dependency drops one skill rather than blocking boot (DESIGN.md:96-105).
"""
import importlib
import os

from core.config import PROJECT_ROOT

NO_ACTION = "none"


def discover_skills() -> dict:
    """Import every module under skills/ and instantiate the ones exposing setup()."""
    active_skills = {}
    skills_dir = PROJECT_ROOT / "skills"

    for file_path in skills_dir.rglob("*.py"):
        if file_path.name == "__init__.py":
            continue

        # Convert absolute path to relative module name
        relative_path = file_path.relative_to(PROJECT_ROOT)
        module_name = str(relative_path.with_suffix("")).replace(os.sep, ".")
        print(f"[*] Discovering: {module_name} at {file_path}")
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "setup"):
                skill_instance = module.setup()
                skill_name = skill_instance.manifest["name"]
                active_skills[skill_name] = skill_instance
                print(f"[+] Loaded: {skill_name}")
        except Exception as e:
            print(f"[-] Failed to load {module_name}: {e}")

    return active_skills


def build_action_schema(active_skills: dict) -> dict:
    """Derive the structured-output schema from the existing manifests.

    Constraining `action` to an enum of real skill names is what makes a
    hallucinated tool name impossible rather than merely unlikely.

    Every field is required. When `action` was optional, llama3.1 answered with
    a thought and no action at all — it narrated a plan and selected nothing.
    Requiring the field is what forces a commitment; `"none"` is how the model
    declines rather than by omission.
    """
    return {
        "type": "object",
        "properties": {
            "thought": {
                "type": "string",
                "description": "Spoken aloud before acting. Always required.",
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
