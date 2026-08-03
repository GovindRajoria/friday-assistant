# skills/os_control/clipboard.py
"""Read and write the system clipboard.

Small, and it closes a real gap: without it the assistant can be asked to
summarise something only by having it retyped. With it, "what did I just
copy?" and "put that on my clipboard" both work.

Not marked destructive. Reading is passive, and a clipboard write replaces
one transient buffer that any application overwrites constantly — it destroys
nothing on disk. That reasoning is worth stating because manage_files takes
the opposite view for the same "it overwrites something" argument: the
difference is that a file is durable and a clipboard is not.

tkinter rather than a new dependency: it ships with CPython, owns a clipboard
implementation on every platform, and pyautogui — already a dependency — has
no clipboard API of its own.
"""
MAX_READ_CHARS = 4000


def _with_hidden_root(action):
    """Run `action(root)` against a withdrawn Tk root and always destroy it.

    tkinter is a GUI toolkit being used headlessly here. Without withdraw()
    an empty window flashes on screen, and without destroy() the interpreter
    keeps a live Tk instance around for the life of the process.
    """
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    try:
        return action(root)
    finally:
        root.destroy()


class ClipboardSkill:
    def __init__(self):
        self.manifest = {
            "name": "clipboard",
            "description": (
                "Reads what is currently on the system clipboard, or copies text onto "
                "it. Use this when asked what was just copied, or to put something "
                "where the operator can paste it. Parameters: 'action' ('read' or "
                "'write') and 'text' (for write)."
            ),
            "parameters": ["action", "text"],
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "read").lower()

        if action not in {"read", "write"}:
            return {"status": "error", "message": f"Unknown clipboard action: {action}"}

        try:
            if action == "read":
                return self._read()
            return self._write(params.get("text"))
        except ImportError as error:
            return {"status": "error", "message": f"Clipboard access is not available here: {error}"}
        except Exception as error:  # noqa: BLE001 — tkinter raises TclError for an empty clipboard
            return {"status": "error", "message": f"Could not {action} the clipboard: {error}"}

    def _read(self) -> dict:
        try:
            text = _with_hidden_root(lambda root: root.clipboard_get())
        except Exception:  # noqa: BLE001 — TclError, but importing tkinter to name it defeats the lazy import
            # An empty clipboard raises rather than returning "". That is a
            # normal state, not a failure, so it gets a plain answer.
            return {"status": "success", "message": "The clipboard is empty, or holds something that is not text."}

        truncated = len(text) > MAX_READ_CHARS
        shown = text[:MAX_READ_CHARS] + (" […truncated]" if truncated else "")
        return {"status": "success", "message": f"Clipboard contents:\n{shown}",
                "data": {"characters": len(text), "truncated": truncated}}

    def _write(self, text) -> dict:
        if text is None or str(text) == "":
            return {"status": "error", "message": "I need some text to put on the clipboard."}
        payload = str(text)

        def put(root):
            root.clipboard_clear()
            root.clipboard_append(payload)
            # Without this the clipboard is owned by a Tk instance that is
            # about to be destroyed, and the contents vanish with it.
            root.update()

        _with_hidden_root(put)
        preview = payload if len(payload) <= 80 else payload[:80] + "…"
        return {"status": "success", "message": f"Copied to the clipboard: {preview}"}


def setup():
    return ClipboardSkill()
