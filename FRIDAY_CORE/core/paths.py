# core/paths.py
"""The filesystem allowlist, shared by every skill that touches the disk.

This logic started inside `skills/os_control/manage_files.py`, which was the
only skill that could reach the filesystem. It moved here when that stopped
being true: `read_document`, `read_spreadsheet`, `search_files`, `screenshot`
and `annotate_image` all take a path from the model, and every one of them
would otherwise have needed its own containment check — five chances to get
subtly different, on the layer whose whole job is that a confused model cannot
*propose* touching `C:\\Windows`.

The rule is unchanged from the original: resolve first, then test containment,
and refuse anything that lands outside every root rather than clamping it into
the nearest one. `Path.resolve()` eliminates `..` and follows symlinks already
on disk, so the check sees where a path really goes rather than how it is
spelled.

Read-only skills use this too. Containment is not only about writes — reading
`~/.ssh/id_rsa` and handing it to a model is its own disclosure.
"""
from pathlib import Path

from core.config import SETTINGS


def allowed_roots(settings: dict | None = None) -> list[Path]:
    """The configured roots, expanded.

    Read at call time rather than at import or in a skill's ``setup()``: a
    missing or malformed ``filesystem`` section then raises inside
    ``execute()``, which the graph already turns into an error Observation,
    instead of inside ``setup()``, where ``core/registry.py`` would drop the
    skill from the registry with nothing but a printed line.
    """
    settings = settings or SETTINGS
    return [Path(root).expanduser() for root in settings["filesystem"]["allowed_roots"]]


def resolve_within(path_str: str, roots: list[Path] | None = None) -> Path | None:
    """Resolve ``path_str`` and return it only if it lands inside an allowed root.

    Returns None for a refusal, so callers must handle it — an exception would
    be easier to swallow by accident.
    """
    roots = roots if roots is not None else allowed_roots()
    if not roots:
        return None

    try:
        candidate = Path(path_str).expanduser()
        if not candidate.is_absolute():
            candidate = roots[0] / candidate
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None

    for root in roots:
        root_resolved = root.resolve(strict=False)
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    return None


def refusal(path_str: str) -> dict:
    """The standard refusal, so every skill words it the same way."""
    return {
        "status": "error",
        "message": (f"'{path_str}' is outside the allowed workspace and was refused. "
                    "Widen filesystem.allowed_roots in config/settings.yaml if that is wrong."),
    }
