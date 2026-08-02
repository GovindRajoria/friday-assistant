# skills/os_control/window_control.py
"""Window management: list open windows, focus, minimise, maximise one by title.

Windows-only, driven directly through ctypes/user32 rather than pywin32 or
pygetwindow, so no extra dependency is needed beyond the standard library —
`ctypes` itself imports cleanly everywhere; only the `windll` attribute is
Windows-specific, so every reference to it is guarded behind IS_WINDOWS and
kept inside methods, never at module scope. On other platforms the skill
still loads and reports a clean "unsupported OS" error instead of vanishing
from the registry, the same pattern skills/os_control/media_control.py uses.

Not destructive. Nothing here deletes data, overwrites a file, or
synthesises input — it only rearranges windows that already exist on
screen — which is why its manifest carries no "destructive" key and it
never routes through the confirmation gate.
"""
import platform

IS_WINDOWS = platform.system() == "Windows"

SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9

MAX_LISTED_TITLES = 20


class WindowControlSkill:
    def __init__(self):
        self.manifest = {
            "name": "window_control",
            "description": (
                "Lists open windows, or focuses, minimises, or maximises one by a "
                "substring of its title. Windows only; reports a clean error on other "
                "platforms. Parameters: 'action' (list, focus, minimize, maximize), "
                "'title' (required except for 'list')."
            ),
            "parameters": ["action", "title"],
        }

    def _enumerate(self):
        """Return [(hwnd, title), ...] for every visible, titled top-level window."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        windows = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    windows.append((hwnd, buffer.value))
            return True

        user32.EnumWindows(_callback, 0)
        return windows

    def _find(self, title_fragment: str):
        fragment = title_fragment.lower()
        for hwnd, title in self._enumerate():
            if fragment in title.lower():
                return hwnd, title
        return None, None

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action", "list")).lower()

        if not IS_WINDOWS:
            return {
                "status": "error",
                "message": f"Window control is only wired up for Windows; this host is {platform.system()}.",
            }

        import ctypes
        user32 = ctypes.windll.user32

        if action == "list":
            titles = [title for _, title in self._enumerate()]
            shown = titles[:MAX_LISTED_TITLES]
            return {
                "status": "success",
                "message": f"{len(titles)} window(s) open: " + ("; ".join(shown) or "(none)"),
                "data": {"windows": titles},
            }

        title_fragment = params.get("title")
        if not title_fragment:
            return {"status": "error", "message": "I need a window title to act on."}

        hwnd, title = self._find(title_fragment)
        if hwnd is None:
            return {"status": "error", "message": f"No open window matches '{title_fragment}'."}

        if action == "focus":
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return {"status": "success", "message": f"Focused '{title}'."}
        if action == "minimize":
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            return {"status": "success", "message": f"Minimised '{title}'."}
        if action == "maximize":
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            return {"status": "success", "message": f"Maximised '{title}'."}

        return {"status": "error", "message": f"Unknown window action: {action}"}


def setup():
    return WindowControlSkill()
