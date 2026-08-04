# skills/dev/search_code.py
"""ripgrep a project and return matches as file:line — the most-used developer verb.

Distinct from `search_files` next door, which searches the *workspace* for
documents. This searches a configured source tree, returns file:line so the answer
can be acted on, and knows what a source tree contains: it skips build output and
dependency directories, and can be narrowed to one language by extension.

ripgrep when it is on PATH, a bounded Python scan when it is not. Measured on this
machine 2026-08-04: `shutil.which("rg")` is None, so the scan is what actually
runs here and the ripgrep branch is unexercised on this hardware.
"""
import shutil
import subprocess

from core.project_roots import outside, resolve_in, roots, unconfigured

TIMEOUT_SECONDS = 25
MAX_MATCHES = 50
MAX_LINE_CHARS = 200
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "friday_env",
             "dist", "build", "out", "release", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", "site-packages", ".next", "coverage"}
MAX_FILE_BYTES = 2_000_000


class SearchCodeSkill:
    def __init__(self):
        self.manifest = {
            "name": "search_code",
            "description": (
                "Searches the source code of a configured project and returns matches as "
                "file and line number. Use this to find where something is defined, called "
                "or configured in a codebase. Parameters: 'pattern' to search for, 'path' "
                "for the project, and optionally 'extension' like 'py' or 'ts' to narrow it. "
                "Read-only. Use search_files for documents in the workspace rather than code."
            ),
            "parameters": ["pattern", "path", "extension"],
        }

    def execute(self, params=None):
        params = params or {}
        pattern = str(params.get("pattern") or "").strip()
        if not pattern:
            return {"status": "error", "message": "I need something to search the code for."}
        if not roots("projects"):
            return unconfigured("projects", "search a project")

        resolved = resolve_in("projects", params.get("path"))
        if resolved is None:
            return outside("projects", str(params.get("path")))
        if not resolved.is_dir():
            return {"status": "error", "message": f"'{resolved}' is not a directory."}

        extension = str(params.get("extension") or "").strip().lstrip("*.")
        ripgrep = shutil.which("rg")
        if ripgrep:
            result, error = self._ripgrep(ripgrep, pattern, resolved, extension)
            if error is None:
                return result
        return self._scan(pattern, resolved, extension)

    def _ripgrep(self, ripgrep, pattern, root, extension):
        command = [ripgrep, "--line-number", "--no-heading", "--color", "never",
                   "--smart-case", "--fixed-strings", "--max-filesize", "2M",
                   "--max-count", "5"]
        if extension:
            command += ["--glob", f"*.{extension}"]
        command += [pattern, str(root)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, error
        if completed.returncode not in (0, 1):
            return None, RuntimeError(completed.stderr.strip())

        lines = [line for line in completed.stdout.splitlines() if line.strip()][:MAX_MATCHES]
        return self._render(pattern, lines, root, extension, "ripgrep"), None

    def _scan(self, pattern, root, extension):
        needle = pattern.lower()
        glob = f"*.{extension}" if extension else "*"
        lines = []
        for path in root.rglob(glob):
            if len(lines) >= MAX_MATCHES:
                break
            if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="strict") as handle:
                    for number, line in enumerate(handle, start=1):
                        if needle in line.lower():
                            lines.append(f"{path}:{number}:{line.strip()[:MAX_LINE_CHARS]}")
                            if len(lines) >= MAX_MATCHES:
                                break
            except (OSError, UnicodeDecodeError):
                continue                       # binary or unreadable
        return self._render(pattern, lines, root, extension, "a plain scan (ripgrep not on PATH)")

    @staticmethod
    def _render(pattern, lines, root, extension, engine):
        scope = f"{root.name}" + (f" (*.{extension} only)" if extension else "")
        if not lines:
            return {
                "status": "success",
                "message": f"No match for '{pattern}' in {scope}, searched with {engine}.",
                "data": {"matches": 0, "engine": engine},
            }
        capped = f" (first {MAX_MATCHES})" if len(lines) >= MAX_MATCHES else ""
        body = "\n".join(f"  {line}" for line in lines)
        return {
            "status": "success",
            "message": f"{len(lines)} match(es){capped} for '{pattern}' in {scope}, via {engine}:\n{body}",
            "data": {"matches": len(lines), "engine": engine},
        }


def setup():
    return SearchCodeSkill()
