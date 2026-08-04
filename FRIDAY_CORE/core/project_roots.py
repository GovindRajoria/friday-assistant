# core/project_roots.py
"""Containment for the development skills, kept apart from the workspace allowlist.

Three separate permissions, deliberately not one:

  filesystem.allowed_roots  a workspace where files are written and deleted
  projects.allowed_roots    source trees that are only ever read
  commands.allowed_roots    directories where something may be executed

Collapsing them would mean that letting FRIDAY describe a repository also let it
run a test suite in that repository, and that letting it read a project also let
it delete files there. They are different questions and they get different
answers.

All three default to refusing everything rather than to a convenient guess. A
skill that says "configure projects.allowed_roots" on its first use is a small
annoyance; a skill that quietly defaults to the whole home directory is a
different class of thing.
"""
from pathlib import Path

from core.config import SETTINGS


def roots(section: str, settings: dict | None = None) -> list[Path]:
    """Configured roots for `projects` or `commands`."""
    settings = settings or SETTINGS
    configured = (settings.get(section) or {}).get("allowed_roots") or []
    return [Path(str(root)).expanduser() for root in configured]


def resolve_in(section: str, path_str: str | None, settings: dict | None = None) -> Path | None:
    """Resolve `path_str` inside `section`'s roots, or None.

    An empty or missing path means the first configured root, because "what
    branch am I on" with no path is a reasonable thing to ask when exactly one
    project is configured.
    """
    configured = roots(section, settings)
    if not configured:
        return None

    if not path_str or not str(path_str).strip():
        return configured[0].resolve(strict=False)

    try:
        candidate = Path(str(path_str)).expanduser()
        if not candidate.is_absolute():
            candidate = configured[0] / candidate
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None

    for root in configured:
        root_resolved = root.resolve(strict=False)
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    return None


def unconfigured(section: str, purpose: str) -> dict:
    """The message for "you have not told me where I may do this yet"."""
    return {
        "status": "error",
        "message": (f"No {section} roots are configured, so I cannot {purpose}. "
                    f"Add absolute paths to {section}.allowed_roots in "
                    "config/settings.yaml — it starts empty deliberately."),
    }


def outside(section: str, path_str: str) -> dict:
    return {
        "status": "error",
        "message": (f"'{path_str}' is not inside any configured {section} root, so it was "
                    f"refused. Configured: "
                    f"{', '.join(str(r) for r in roots(section)) or '(none)'}."),
    }
