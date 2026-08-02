# skills/os_control/send_keys.py
"""Synthetic keyboard input: type a literal string, or press a named key combination.

Deliberately narrow. No mouse control and no arbitrary screen-coordinate
clicks are implemented here — a synthetic click at a pixel position is the
least useful and most dangerous thing this module could do, since its effect
depends entirely on whatever happens to be under that pixel at the moment it
fires, and nothing raised for this phase needs it. Typing and hotkeys cover
every legitimate automation case this skill exists for.

pyautogui is imported lazily inside execute(), not at module scope, for the
same reason core/speaker.py and skills/os_control/media_control.py do it: CI
does not install it (tests/test_imports_without_runtime_deps.py masks it
explicitly), and a module-scope import here would make the whole import
chain server/app.py sits in uncollectable on the runner.

Destructive by manifest, not because it deletes anything, but because it
synthesises input into whatever window currently has focus — which the
operator does not necessarily control at the moment this fires. See the
Known limitations section of the README.
"""


class SendKeysSkill:
    def __init__(self):
        self.manifest = {
            "name": "send_keys",
            "description": (
                "Synthesises keyboard input into whatever window currently has focus: "
                "either types a literal string, or presses a named key combination, e.g. "
                "['ctrl','s']. Parameters: 'text' (a string to type) or 'keys' (a list of "
                "key names to press together) — exactly one of the two is required."
            ),
            "parameters": ["text", "keys"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        text = params.get("text")
        keys = params.get("keys")

        if not text and not keys:
            return {"status": "error", "message": "I need either 'text' to type or 'keys' to press."}
        if text and keys:
            return {"status": "error", "message": "Provide either 'text' or 'keys', not both."}

        try:
            import pyautogui
        except ImportError:
            return {"status": "error", "message": "pyautogui is not installed, so keystrokes cannot be synthesised."}

        try:
            if text:
                pyautogui.typewrite(str(text), interval=0.02)
                return {"status": "success", "message": f"Typed {len(str(text))} character(s)."}

            if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
                return {"status": "error", "message": "'keys' must be a non-empty list of key name strings."}
            pyautogui.hotkey(*keys)
            return {"status": "success", "message": f"Pressed {'+'.join(keys)}."}
        except Exception as exc:
            return {"status": "error", "message": f"Keystroke synthesis failed: {exc}"}


def setup():
    return SendKeysSkill()
