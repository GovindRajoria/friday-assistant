# skills/os_control/power_control.py
"""Lock, sleep, shut down, restart. Lock is the one actually worth having.

Everything here is destructive and goes through the confirmation gate, but they are
not equally serious and the skill does not pretend otherwise:

  lock     instant, reversible with a password, and the genuinely useful one —
           "lock the screen, I'm leaving" is a sentence people say
  sleep    reversible, loses nothing
  restart  closes everything; unsaved work is at the mercy of each application
  shutdown same, and the machine is then off, so the assistant cannot undo it or
           even report that it worked

The last two get a delay rather than firing immediately, so a confirmation given
too quickly is still recoverable from a terminal. And the reply for those is
written *before* the command runs, because a process being shut down cannot be
relied on to report anything afterwards.

Windows-only in implementation, and it says so on other platforms rather than
failing at import — the same pattern as `media_control` and `window_control`.
"""
import platform
import subprocess

IS_WINDOWS = platform.system() == "Windows"
# Seconds between "yes" and the machine actually going down. Enough to run
# `shutdown /a` and stop it.
ABORTABLE_DELAY_SECONDS = 20


class PowerControlSkill:
    def __init__(self):
        self.manifest = {
            "name": "power_control",
            "description": (
                "Locks the screen, puts the machine to sleep, restarts it, or shuts it down. "
                "Parameters: 'action' (lock, sleep, restart, shutdown, cancel). Locking is "
                "instant and safe; restarting and shutting down close every application and "
                "are delayed by twenty seconds so they can be cancelled. Every call requires "
                "confirmation. Use this only when explicitly asked to lock, sleep, restart or "
                "shut down the computer."
            ),
            "parameters": ["action"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "").lower().strip()

        if not IS_WINDOWS:
            return {
                "status": "error",
                "message": f"Power control is only implemented for Windows; this host is {platform.system()}.",
            }
        if not action:
            return {"status": "error", "message": "Which power action — lock, sleep, restart or shutdown?"}

        handlers = {
            "lock": self._lock,
            "sleep": self._sleep,
            "suspend": self._sleep,
            "restart": lambda: self._delayed("restart", ["/r"]),
            "reboot": lambda: self._delayed("restart", ["/r"]),
            "shutdown": lambda: self._delayed("shut down", ["/s"]),
            "shut_down": lambda: self._delayed("shut down", ["/s"]),
            "poweroff": lambda: self._delayed("shut down", ["/s"]),
            "cancel": self._cancel,
            "abort": self._cancel,
        }
        handler = handlers.get(action)
        if handler is None:
            return {
                "status": "error",
                "message": (f"'{action}' is not a power action I know. "
                            "I can lock, sleep, restart, shut down, or cancel a pending shutdown."),
            }
        return handler()

    def _lock(self):
        try:
            # rundll32 rather than the shutdown binary: locking is not a power
            # state transition and shutdown.exe cannot do it.
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"],
                           check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as error:
            return {"status": "error", "message": f"Could not lock the screen: {error}"}
        return {"status": "success", "message": "Screen locked.", "data": {"action": "lock"}}

    def _sleep(self):
        try:
            # SetSuspendState's first argument is 0 for sleep, 1 for hibernate.
            # The third disables wake events, which is why it is 0 here — an
            # alarm should still be able to wake the machine.
            subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                           check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as error:
            return {"status": "error", "message": f"Could not put the machine to sleep: {error}"}
        return {"status": "success", "message": "Going to sleep.", "data": {"action": "sleep"}}

    def _delayed(self, described, flags):
        """Schedule a restart or shutdown, abortable. Message built before firing."""
        command = ["shutdown", *flags, "/t", str(ABORTABLE_DELAY_SECONDS), "/c",
                   "Requested through FRIDAY"]
        # Composed first: once the machine is going down, nothing here is
        # guaranteed to run, and a reply assembled afterwards may never be sent.
        message = (f"The machine will {described} in {ABORTABLE_DELAY_SECONDS} seconds. "
                   f"Say cancel, or run 'shutdown /a', to stop it.")
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            return {"status": "error", "message": f"Could not schedule the {described}: {error}"}
        if result.returncode != 0:
            return {
                "status": "error",
                "message": (f"Windows refused the {described}: "
                            f"{result.stderr.strip() or f'exit {result.returncode}'}"),
            }
        return {"status": "success", "message": message,
                "data": {"action": described, "delay_seconds": ABORTABLE_DELAY_SECONDS}}

    def _cancel(self):
        try:
            result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as error:
            return {"status": "error", "message": f"Could not cancel: {error}"}
        if result.returncode != 0:
            return {
                "status": "success",
                "message": "There was no pending shutdown or restart to cancel.",
                "data": {"action": "cancel", "cancelled": False},
            }
        return {"status": "success", "message": "Cancelled the pending shutdown.",
                "data": {"action": "cancel", "cancelled": True}}


def setup():
    return PowerControlSkill()
