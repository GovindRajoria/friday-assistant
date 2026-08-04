# skills/os_control/manage_processes.py
"""List processes, and kill one by name. The kill is gated; the listing is not free either.

Marked destructive as a whole, which means listing prompts for confirmation too.
That is the same trade `manage_files` makes and for the same reason: `destructive`
is one flag per skill and `action` is a model-supplied parameter routing never
sees. Splitting it in two to avoid a prompt on `list` is not worth the manifest
surface.

Three protections on the kill, because "kill it by name" is a blunt instrument:

**Never itself.** Killing the process tree this code runs in would take the
assistant down mid-sentence, and the backend is `python.exe` — the same name as
plenty of legitimate targets.

**Never a critical system process.** A name match against `csrss`, `winlogon`,
`lsass` and friends is refused outright. Confirmation is a human check, and a human
who is told "shall I close python.exe?" may reasonably say yes without knowing it
is the process asking.

**Reports what it would kill, and refuses ambiguity.** Six processes called
`chrome.exe` is the normal case, so the count goes in the message before anything
happens, and the confirmation the human sees names it.
"""
import os

# Killing any of these makes the machine unusable or triggers a forced restart.
# Names are compared case-insensitively, without the extension.
PROTECTED = {
    "system", "system idle process", "registry", "smss", "csrss", "wininit",
    "winlogon", "services", "lsass", "lsm", "svchost", "dwm", "explorer",
    "fontdrvhost", "sihost", "ctfmon", "audiodg", "conhost", "kernel_task",
    "launchd", "systemd", "init",
}
MAX_LISTED = 25


class ManageProcessesSkill:
    def __init__(self):
        self.manifest = {
            "name": "manage_processes",
            "description": (
                "Lists running processes with their memory and CPU use, or ends a process by "
                "name. Parameters: 'action' (list or kill), 'name' for the process, and "
                "optionally 'sort' (memory or cpu). Ending a process loses whatever it had "
                "unsaved, so every call requires confirmation. Use system_check for overall "
                "CPU and memory instead of a per-process breakdown."
            ),
            "parameters": ["action", "name", "sort"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        try:
            import psutil
        except ImportError as error:
            return {"status": "error", "message": f"Process control needs psutil: {error}"}

        action = str(params.get("action") or "list").lower()
        if action in {"kill", "end", "stop", "terminate", "close"}:
            return self._kill(psutil, str(params.get("name") or "").strip())
        if action in {"list", "show", "top"}:
            return self._list(psutil, str(params.get("sort") or "memory").lower(),
                              str(params.get("name") or "").strip())
        return {"status": "error", "message": f"Unknown process action '{action}'. Use list or kill."}

    def _list(self, psutil, sort, name_filter):
        rows = []
        for process in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
            try:
                info = process.info
                if name_filter and name_filter.lower() not in (info["name"] or "").lower():
                    continue
                memory = info["memory_info"].rss if info["memory_info"] else 0
                rows.append((memory, info["cpu_percent"] or 0.0, info["pid"], info["name"] or "?"))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not rows:
            scope = f" matching '{name_filter}'" if name_filter else ""
            return {"status": "success", "message": f"No processes found{scope}.",
                    "data": {"count": 0}}

        rows.sort(key=lambda row: row[1] if sort.startswith("cpu") else row[0], reverse=True)
        shown = rows[:MAX_LISTED]
        lines = [f"  {name} (pid {pid}) — {memory / 1024 ** 2:.0f} MB, {cpu:.0f}% CPU"
                 for memory, cpu, pid, name in shown]
        scope = f" matching '{name_filter}'" if name_filter else ""
        return {
            "status": "success",
            "message": (f"{len(rows)} process(es){scope}; top {len(shown)} by "
                        f"{'CPU' if sort.startswith('cpu') else 'memory'}:\n" + "\n".join(lines)),
            "data": {"count": len(rows)},
        }

    def _kill(self, psutil, name):
        if not name:
            return {"status": "error", "message": "Which process should I end?"}

        stem = name.lower().removesuffix(".exe")
        if stem in PROTECTED:
            return {
                "status": "error",
                "message": (f"'{name}' is a critical system process and I will not end it — "
                            "the machine would become unusable or restart. This is refused "
                            "before the confirmation prompt, not by it."),
            }

        own_pid = os.getpid()
        try:
            own_tree = {own_pid} | {child.pid for child in psutil.Process(own_pid).children(recursive=True)}
            parent_chain = {parent.pid for parent in psutil.Process(own_pid).parents()}
        except Exception:                                             # noqa: BLE001
            own_tree, parent_chain = {own_pid}, set()

        targets, skipped_self = [], False
        for process in psutil.process_iter(["pid", "name"]):
            try:
                if stem != (process.info["name"] or "").lower().removesuffix(".exe"):
                    continue
                if process.info["pid"] in own_tree or process.info["pid"] in parent_chain:
                    skipped_self = True
                    continue
                targets.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not targets:
            if skipped_self:
                return {
                    "status": "error",
                    "message": (f"The only process called '{name}' is the one I am running in, "
                                "so I will not end it — that would stop me mid-sentence."),
                }
            return {"status": "error", "message": f"No running process is called '{name}'."}

        ended, failed = [], []
        for process in targets:
            try:
                process.terminate()
                ended.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
                failed.append(f"pid {process.pid} ({type(error).__name__})")

        # Give them a moment to exit cleanly, then report honestly on any that
        # did not — "terminated" is a request, not an outcome.
        gone, alive = psutil.wait_procs([p for p in targets if p.pid in ended], timeout=3)
        note = ""
        if alive:
            note = (f" {len(alive)} did not exit within 3s and may still be running; "
                    "I did not escalate to a forced kill.")
        if failed:
            note += f" Could not end: {', '.join(failed)}."
        mine = " I left my own process alone." if skipped_self else ""

        return {
            "status": "success" if gone or ended else "error",
            "message": f"Ended {len(gone)} of {len(targets)} process(es) called '{name}'.{note}{mine}",
            "data": {"requested": len(targets), "ended": len(gone), "still_running": len(alive)},
        }


def setup():
    return ManageProcessesSkill()
