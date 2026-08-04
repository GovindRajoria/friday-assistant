# skills/reading/search_files.py
"""Find files by name or by content, inside the allowlisted roots only.

Read-only, and containment is not a formality here: an unbounded content search
is the fastest way to read something it was never meant to see. Every root comes
from `filesystem.allowed_roots`, and an explicit `path` is resolved through the
same check as everything else — a search cannot be pointed at C:\\Users by asking
nicely.

ripgrep does the content search when it is on PATH, because it respects
.gitignore and is an order of magnitude faster on a real tree; `pathlib` plus a
line scan is the fallback so the skill works on a machine without it. The
fallback is not a lesser feature to be apologised for, it is the reason this does
not silently stop working — but it *does* read files ripgrep would have skipped,
so it applies its own binary and size guards.

Measured on the development machine 2026-08-04: `shutil.which("rg")` returns
None — there is no ripgrep binary installed here, whatever an interactive shell's
`rg` alias suggests — so **the fallback is the path that actually runs**, and it
is the one that has been exercised. The ripgrep branch is written and unit-tested
against its exit codes (0 and 1 both mean success, 1 being "no matches"), but has
never run against a real ripgrep on this machine.
"""
import shutil
import subprocess

from core.paths import allowed_roots, refusal, resolve_within

MAX_RESULTS = 40
SEARCH_TIMEOUT_SECONDS = 20
# Skipped by the fallback scanner: build output and dependency trees turn a
# useful search into thousands of matches nobody asked about.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "friday_env",
             "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", "release"}
MAX_SCAN_BYTES = 2_000_000


class SearchFilesSkill:
    def __init__(self):
        self.manifest = {
            "name": "search_files",
            "description": (
                "Finds files on this computer inside the allowed workspace, by filename "
                "pattern or by text inside them. Parameters: 'pattern' (what to look for), "
                "'mode' (name or content), and optionally 'path' to search under. Read-only. "
                "Use search_code for a source tree with file:line matches; use this to "
                "locate a document before reading it with read_document."
            ),
            "parameters": ["pattern", "mode", "path"],
        }

    def execute(self, params=None):
        params = params or {}
        pattern = (params.get("pattern") or "").strip()
        if not pattern:
            return {"status": "error", "message": "I need something to search for."}

        roots = allowed_roots()
        if not roots:
            return {"status": "error", "message": "No searchable roots are configured."}

        if params.get("path"):
            resolved = resolve_within(str(params["path"]), roots)
            if resolved is None:
                return refusal(str(params["path"]))
            if not resolved.is_dir():
                return {"status": "error", "message": f"'{resolved}' is not a directory."}
            search_roots = [resolved]
        else:
            search_roots = [root for root in roots if root.exists()]

        if not search_roots:
            return {
                "status": "error",
                "message": (f"None of the configured roots exist yet: "
                            f"{', '.join(str(r) for r in roots)}."),
            }

        mode = str(params.get("mode") or "name").lower()
        if mode in {"content", "text", "grep", "inside"}:
            return self._by_content(pattern, search_roots)
        return self._by_name(pattern, search_roots)

    def _by_name(self, pattern, roots):
        # A bare word is what the model usually supplies; make it a substring
        # match rather than returning nothing for "invoice" against "invoice.pdf".
        globbed = pattern if any(ch in pattern for ch in "*?[") else f"*{pattern}*"
        hits = []
        for root in roots:
            for path in root.rglob(globbed):
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                hits.append(path)
                if len(hits) >= MAX_RESULTS:
                    break
            if len(hits) >= MAX_RESULTS:
                break

        if not hits:
            return {
                "status": "success",
                "message": f"No file matching '{pattern}' under {self._describe(roots)}.",
                "data": {"matches": 0},
            }
        lines = [f"  {path}" + ("/" if path.is_dir() else f"  ({self._size(path)})") for path in hits]
        capped = f" (first {MAX_RESULTS})" if len(hits) >= MAX_RESULTS else ""
        return {
            "status": "success",
            "message": f"{len(hits)} match(es){capped} for '{pattern}':\n" + "\n".join(lines),
            "data": {"matches": len(hits), "paths": [str(p) for p in hits]},
        }

    def _by_content(self, pattern, roots):
        ripgrep = shutil.which("rg")
        if ripgrep:
            found, error = self._ripgrep(ripgrep, pattern, roots)
            if error is None:
                return found
            # Fall through rather than fail: a ripgrep that is present but
            # unhappy should not make the skill useless.
        return self._scan(pattern, roots)

    def _ripgrep(self, ripgrep, pattern, roots):
        command = [ripgrep, "--line-number", "--no-heading", "--color", "never",
                   "--max-count", "3", "--fixed-strings", "--smart-case",
                   "--max-filesize", "2M", pattern, *[str(r) for r in roots]]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=SEARCH_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, error

        # rg exits 1 for "no matches", which is not an error.
        if result.returncode not in (0, 1):
            return None, RuntimeError(result.stderr.strip() or f"exit {result.returncode}")

        lines = [line for line in result.stdout.splitlines() if line.strip()][:MAX_RESULTS]
        return self._content_result(pattern, lines, roots, "ripgrep"), None

    def _scan(self, pattern, roots):
        needle = pattern.lower()
        lines = []
        for root in roots:
            for path in root.rglob("*"):
                if len(lines) >= MAX_RESULTS:
                    break
                if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
                    continue
                try:
                    if path.stat().st_size > MAX_SCAN_BYTES:
                        continue
                    with open(path, "r", encoding="utf-8", errors="strict") as handle:
                        for number, line in enumerate(handle, start=1):
                            if needle in line.lower():
                                lines.append(f"{path}:{number}:{line.strip()[:200]}")
                                break
                except (OSError, UnicodeDecodeError):
                    continue        # binary or unreadable; a decode error is the binary test
        return self._content_result(pattern, lines, roots, "a plain scan (ripgrep not on PATH)")

    def _content_result(self, pattern, lines, roots, engine):
        if not lines:
            return {
                "status": "success",
                "message": f"No file under {self._describe(roots)} contains '{pattern}' (searched with {engine}).",
                "data": {"matches": 0, "engine": engine},
            }
        capped = f" (first {MAX_RESULTS})" if len(lines) >= MAX_RESULTS else ""
        body = "\n".join(f"  {line}" for line in lines)
        return {
            "status": "success",
            "message": f"{len(lines)} match(es){capped} for '{pattern}', via {engine}:\n{body}",
            "data": {"matches": len(lines), "engine": engine},
        }

    @staticmethod
    def _describe(roots):
        return ", ".join(str(root) for root in roots)

    @staticmethod
    def _size(path):
        try:
            size = path.stat().st_size
        except OSError:
            return "size unknown"
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


def setup():
    return SearchFilesSkill()
