# skills/utility/disk_report.py
"""What is eating the drive, and how much room is actually left.

Written because this exact problem cost a session: C: was down to 946 MB during a
speech-model benchmark, the download failed part-way, and the symptom was a model
that would not load rather than anything mentioning disk space.

Two halves. Every drive's free space, which is the fast answer. And, on request,
the largest directories under a path — which is the slow answer and is why that
part only runs when asked, with a depth bound. Walking a whole drive to add up
file sizes is minutes of I/O, and an assistant that silently does that when asked
"how much space is left" is worse than one that cannot answer.
"""
import shutil
from pathlib import Path

from core.paths import allowed_roots, refusal, resolve_within

TOP_N = 10
MAX_DEPTH = 2


class DiskReportSkill:
    def __init__(self):
        self.manifest = {
            "name": "disk_report",
            "description": (
                "Reports free and used space on every drive, and can find which directories "
                "are taking the most room under a path in the workspace. Parameters: "
                "optionally 'path' to break down what is using space there. Use this when "
                "asked about disk space, a full drive, or what is taking up room. Its answer "
                "is complete — the turn ends when it returns. Use system_check for CPU and "
                "memory rather than storage."
            ),
            "parameters": ["path"],
            "terminal": True,
        }

    def execute(self, params=None):
        params = params or {}
        lines = [self._drives()]

        path_str = params.get("path")
        if path_str:
            resolved = resolve_within(str(path_str), allowed_roots())
            if resolved is None:
                return refusal(str(path_str))
            if not resolved.is_dir():
                return {"status": "error", "message": f"'{resolved}' is not a directory."}
            lines.append(self._breakdown(resolved))

        return {
            "status": "success",
            "message": "\n".join(lines),
            "data": {"analysed": bool(path_str)},
        }

    def _drives(self):
        rows = []
        for mount in self._mounts():
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue                      # an empty card reader or unmapped network drive
            percent = (usage.used / usage.total * 100) if usage.total else 0
            warning = "  <-- nearly full" if usage.free < 5 * 1024 ** 3 else ""
            rows.append(f"  {mount}  {self._size(usage.free)} free of "
                        f"{self._size(usage.total)} ({percent:.0f}% used){warning}")
        return "Drives:\n" + ("\n".join(rows) or "  (none readable)")

    @staticmethod
    def _mounts():
        """Drive letters on Windows, the filesystem root elsewhere."""
        import platform
        import string

        if platform.system() != "Windows":
            return ["/"]
        return [f"{letter}:\\" for letter in string.ascii_uppercase
                if Path(f"{letter}:\\").exists()]

    def _breakdown(self, root: Path):
        """The biggest immediate children, bounded in depth. Slow by nature."""
        sizes = []
        for child in self._children(root):
            total, skipped = self._directory_size(child)
            sizes.append((total, child.name + ("/" if child.is_dir() else ""), skipped))

        sizes.sort(reverse=True)
        rows = [f"  {self._size(total):>10}  {name}" + ("  (some unreadable)" if skipped else "")
                for total, name, skipped in sizes[:TOP_N]]
        counted = self._size(sum(total for total, _, _ in sizes))
        return (f"Largest items directly under {root} (top {min(len(sizes), TOP_N)} of "
                f"{len(sizes)}, {counted} counted):\n" + ("\n".join(rows) or "  (empty)"))

    @staticmethod
    def _children(root: Path):
        try:
            return list(root.iterdir())
        except OSError:
            return []

    def _directory_size(self, path: Path, depth: int = 0):
        """(bytes, hit_something_unreadable). Depth-bounded — this is the slow half."""
        if path.is_file():
            try:
                return path.stat().st_size, False
            except OSError:
                return 0, True
        if depth >= MAX_DEPTH:
            # Beyond the bound, report what is directly here rather than
            # recursing without limit into a dependency tree.
            total, skipped = 0, False
            for child in self._children(path):
                try:
                    if child.is_file():
                        total += child.stat().st_size
                except OSError:
                    skipped = True
            return total, skipped

        total, skipped = 0, False
        for child in self._children(path):
            child_total, child_skipped = self._directory_size(child, depth + 1)
            total += child_total
            skipped = skipped or child_skipped
        return total, skipped

    @staticmethod
    def _size(number: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if number < 1024 or unit == "TB":
                return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
            number /= 1024
        return f"{number:.1f} TB"


def setup():
    return DiskReportSkill()
