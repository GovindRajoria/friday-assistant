# skills/os_control/manage_files.py
"""File operations gated by a path allowlist: list, read, move, delete.

Confirmation (core/nodes/confirm.py) asks a human before anything here runs;
the allowlist below is the layer beneath that. It exists so a confused or
adversarial model cannot even *propose* touching something like
C:\\Windows — refusing the request outright, before a human is ever asked to
rubber-stamp it, is a stronger guarantee than "the human will probably say
no." Confirmation is a backstop for judgment calls; the allowlist makes an
entire class of request unrepresentable regardless of judgment.

The whole skill is marked "destructive" in its manifest, including the
read-only `list` and `read` actions, not only `move` and `delete`. The
manifest contract this project settled on (tools/check_manifests.py) is one
flag per skill, not per action inside it — `action` here is just another
model-supplied parameter, invisible to routing until the skill actually
runs. Splitting this into four skills to get finer-grained gating was
considered and rejected: it would multiply the manifest surface for a
convenience (skipping confirmation on a read) that is not worth the
complexity in a phase whose entire point is the safety gate. See the README.
"""
from pathlib import Path

from core.config import SETTINGS

# Read actions are capped rather than dumped whole into the model's context —
# a multi-megabyte log file would otherwise blow the prompt budget on a
# single Observation.
MAX_READ_CHARS = 4000
MAX_LISTED_ENTRIES = 50


def _allowed_roots() -> list[Path]:
    # Read at call time, not at __init__/setup(), so a missing or malformed
    # `filesystem` section raises inside execute() — which the graph already
    # catches and turns into an error Observation — rather than inside
    # setup(), where core/registry.py's bare `except Exception` would drop
    # this skill from the registry entirely with nothing but a printed line.
    return [Path(root).expanduser() for root in SETTINGS["filesystem"]["allowed_roots"]]


def _resolve_within(path_str: str, roots: list[Path]) -> Path | None:
    """Resolve `path_str` and return it only if it lands inside an allowed root.

    Resolution happens before the containment check, not after, specifically
    to defeat `..` segments and symlinks: Path.resolve() eliminates `..` and
    follows any symlink already present on disk, so the containment check
    below sees the real destination rather than the string that was typed.
    A path that resolves outside every root is refused outright — clamping
    it into the nearest root would silently do something nobody asked for.
    """
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


class ManageFilesSkill:
    def __init__(self):
        self.manifest = {
            "name": "manage_files",
            "description": (
                "Lists, reads, moves, or deletes files and folders, restricted to a "
                "configured workspace allowlist — it cannot touch anything outside it, "
                "not even via '..' or a symlink. Parameters: 'action' (list, read, move, "
                "delete), 'path', and 'destination' (move only). Every call requires "
                "human confirmation before it runs."
            ),
            "parameters": ["action", "path", "destination"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action", "")).lower()
        path_str = params.get("path")

        if action not in {"list", "read", "move", "delete"}:
            return {"status": "error", "message": f"Unknown file action: {action or '(none given)'}"}
        if not path_str:
            return {"status": "error", "message": "I need a path to operate on."}

        roots = _allowed_roots()
        resolved = _resolve_within(path_str, roots)
        if resolved is None:
            return {"status": "error", "message": f"'{path_str}' is outside the allowed workspace and was refused."}

        try:
            if action == "list":
                return self._list(resolved)
            if action == "read":
                return self._read(resolved)
            if action == "move":
                return self._move(resolved, params.get("destination"), roots)
            return self._delete(resolved)
        except OSError as exc:
            return {"status": "error", "message": f"Could not {action} '{resolved}': {exc}"}

    def _list(self, resolved: Path) -> dict:
        if not resolved.exists():
            return {"status": "error", "message": f"'{resolved}' does not exist."}
        if not resolved.is_dir():
            return {"status": "error", "message": f"'{resolved}' is not a directory."}

        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in resolved.iterdir())
        shown = entries[:MAX_LISTED_ENTRIES]
        return {
            "status": "success",
            "message": f"{len(entries)} item(s) in {resolved}: " + (", ".join(shown) or "(empty)"),
            "data": {"entries": entries},
        }

    def _read(self, resolved: Path) -> dict:
        if not resolved.is_file():
            return {"status": "error", "message": f"'{resolved}' is not a readable file."}

        text = resolved.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > MAX_READ_CHARS
        return {
            "status": "success",
            "message": text[:MAX_READ_CHARS] + (" [truncated]" if truncated else ""),
            "data": {"path": str(resolved), "truncated": truncated},
        }

    def _move(self, resolved: Path, destination: str | None, roots: list[Path]) -> dict:
        if not destination:
            return {"status": "error", "message": "I need a destination path to move to."}
        resolved_dest = _resolve_within(destination, roots)
        if resolved_dest is None:
            return {"status": "error", "message": f"'{destination}' is outside the allowed workspace and was refused."}
        if not resolved.exists():
            return {"status": "error", "message": f"'{resolved}' does not exist."}

        resolved_dest.parent.mkdir(parents=True, exist_ok=True)
        resolved.rename(resolved_dest)
        return {"status": "success", "message": f"Moved '{resolved}' to '{resolved_dest}'."}

    def _delete(self, resolved: Path) -> dict:
        if not resolved.exists():
            return {"status": "error", "message": f"'{resolved}' does not exist."}

        if resolved.is_dir():
            import shutil
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        return {"status": "success", "message": f"Deleted '{resolved}'."}


def setup():
    return ManageFilesSkill()
