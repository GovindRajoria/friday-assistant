"""Validate every skill's manifest without importing anything.

The skill registry in `core/main.py` is keyed by `manifest["name"]`, so two
skills declaring the same name silently overwrite each other — the second one
wins and the first disappears with no error anywhere. That is the invariant this
script exists to protect.

It reads the manifests with `ast` rather than importing the modules, for a
reason: the skills import `ultralytics`, `ollama`, `psutil`, `python-docx` and
`requests` at module scope, and `scan_environment.setup()` refuses to construct
without an exported OpenVINO model on disk. A checker that imported them would
need the whole runtime environment to verify a handful of string literals, and
would fail for reasons that have nothing to do with the manifests.

The trade-off: a manifest built dynamically rather than written as a dict
literal is invisible here. That is reported as an error rather than skipped, so
the gap cannot open quietly.

    python tools/check_manifests.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# The description is the only thing the model sees when deciding whether to call
# a skill, so a stub description is a routing bug waiting to happen rather than a
# documentation gap.
MIN_DESCRIPTION_CHARS = 40

REQUIRED_KEYS = {"name", "description"}
# "destructive" is Phase 6: a skill that can delete or overwrite something, or
# synthesise input, sets it so core/graph.py routes to the confirmation node
# instead of straight to `act`. Optional and defaulting to falsy — the nine
# skills that predate this key declare nothing and must keep passing unchanged.
ALLOWED_KEYS = REQUIRED_KEYS | {"parameters", "destructive"}


class Problem(Exception):
    pass


def literal_manifest(tree: ast.Module) -> dict:
    """Return the dict assigned to `self.manifest`, or raise."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "manifest"
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                found.append(node.value)

    if not found:
        raise Problem("no `self.manifest = {...}` assignment")
    if len(found) > 1:
        raise Problem(f"{len(found)} manifest assignments; expected exactly one")

    try:
        manifest = ast.literal_eval(found[0])
    except ValueError as error:
        raise Problem(
            f"manifest is not a literal dict, so it cannot be checked statically: {error}"
        ) from error

    if not isinstance(manifest, dict):
        raise Problem(f"manifest is a {type(manifest).__name__}, expected a dict")
    return manifest


def has_setup(tree: ast.Module) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "setup"
        for node in tree.body
    )


def validate(manifest: dict) -> None:
    missing = REQUIRED_KEYS - manifest.keys()
    if missing:
        raise Problem(f"manifest is missing {sorted(missing)}")

    unknown = manifest.keys() - ALLOWED_KEYS
    if unknown:
        raise Problem(f"manifest has unrecognised keys {sorted(unknown)}")

    name = manifest["name"]
    if not isinstance(name, str) or not name:
        raise Problem("name must be a non-empty string")
    if not name.isidentifier() or name != name.lower():
        raise Problem(
            f"name {name!r} must be a lowercase identifier — the model emits it "
            "verbatim on the Action line"
        )

    description = manifest["description"]
    if not isinstance(description, str):
        raise Problem("description must be a string")
    if len(description) < MIN_DESCRIPTION_CHARS:
        raise Problem(
            f"description is {len(description)} characters; at least "
            f"{MIN_DESCRIPTION_CHARS} are needed for the model to route to it"
        )

    parameters = manifest.get("parameters", [])
    if not isinstance(parameters, list):
        raise Problem(f"parameters must be a list, got {type(parameters).__name__}")
    for parameter in parameters:
        if not isinstance(parameter, str) or not parameter.isidentifier():
            raise Problem(f"parameter {parameter!r} must be an identifier string")

    if "destructive" in manifest and not isinstance(manifest["destructive"], bool):
        raise Problem(
            f"destructive must be a bool, got {type(manifest['destructive']).__name__} — "
            "core/graph.py checks it with a plain truthiness test, so a stray string "
            "like 'true' would route as truthy no matter what it says"
        )


def main() -> None:
    if not SKILLS_DIR.is_dir():
        print(f"FAIL  no skills directory at {SKILLS_DIR}")
        sys.exit(1)

    modules = sorted(
        path for path in SKILLS_DIR.rglob("*.py") if path.name != "__init__.py"
    )
    if not modules:
        print(f"FAIL  no skill modules under {SKILLS_DIR}")
        sys.exit(1)

    errors: list[str] = []
    seen: dict[str, Path] = {}
    rows: list[tuple[str, str, Path]] = []

    for path in modules:
        relative = path.relative_to(SKILLS_DIR.parent)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            manifest = literal_manifest(tree)
            validate(manifest)
            if not has_setup(tree):
                raise Problem(
                    "no module-level `setup()` — core/main.py skips modules without it, "
                    "so this skill would never load"
                )
        except (Problem, SyntaxError) as error:
            errors.append(f"{relative}: {error}")
            continue

        name = manifest["name"]
        if name in seen:
            errors.append(
                f"{relative}: duplicate skill name {name!r}, already declared by "
                f"{seen[name]}. The registry is keyed by name, so one of these "
                "would silently replace the other."
            )
            continue
        seen[name] = relative
        marker = "DESTRUCTIVE" if manifest.get("destructive") else "—"
        rows.append((name, ", ".join(manifest.get("parameters", [])) or "—", marker, relative))

    for name, parameters, marker, relative in rows:
        print(f"OK    {name:<22} {parameters:<28} {marker:<12} {relative}")

    if errors:
        print()
        for error in errors:
            print(f"FAIL  {error}")
        print(f"\n{len(errors)} problem(s) in {len(modules)} skill module(s).")
        sys.exit(1)

    print(f"\n{len(rows)} skills, all manifests valid and all names unique.")


if __name__ == "__main__":
    main()
