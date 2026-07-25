# skills/os_control/media_control.py
"""System audio control.

Volume and mute handling is inherently platform-specific. The Windows path
drives the OS media keys (and falls back to the CoreAudio COM interface for
mute), so the module imports those dependencies lazily — on other platforms the
skill still loads and reports cleanly instead of failing at import time and
disappearing from the skill registry.
"""
import platform

IS_WINDOWS = platform.system() == "Windows"

# Windows' volume keys move in fixed 2% increments, so reaching a target level
# means zeroing out first and then stepping up.
VOLUME_STEP_PERCENT = 2
STEPS_TO_ZERO = 50


class MediaControlSkill:
    def __init__(self):
        self.manifest = {
            "name": "media_control",
            "description": "Controls system audio and playback. Parameters: 'action' (set_volume, mute, play_pause, next_track), 'level' (0-100 for set_volume).",
            "parameters": ["action", "level"]
        }

    def _set_mute_via_coreaudio(self, muted):
        """Mute through the Windows CoreAudio COM interface.

        Returns True when the call succeeded. pycaw's API surface has shifted
        between releases, so this stays defensive and lets the caller fall back
        to media keys.
        """
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL

            devices = AudioUtilities.GetSpeakers()
            activate = getattr(devices, 'Activate', getattr(devices, 'activate', None))
            if not activate:
                return False

            interface = activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMute(1 if muted else 0, None)
            return True
        except Exception:
            return False

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action", "set_volume")).lower()
        level = params.get("level", 50)

        if not IS_WINDOWS:
            return {
                "status": "error",
                "message": f"Audio control is only wired up for Windows; this host is {platform.system()}.",
            }

        try:
            import pyautogui
            import winsound

            if action == "set_volume":
                try:
                    level = max(0, min(100, int(level)))
                except (TypeError, ValueError):
                    return {"status": "error", "message": f"'{level}' is not a valid volume level."}

                # Toggle mute twice to wake the audio driver, then calibrate
                # down to zero and step up to the requested level.
                pyautogui.press("volumemute")
                pyautogui.press("volumemute")

                for _ in range(STEPS_TO_ZERO):
                    pyautogui.press("volumedown")
                for _ in range(level // VOLUME_STEP_PERCENT):
                    pyautogui.press("volumeup")

                winsound.Beep(1000, 200)
                return {"status": "success", "message": f"Audio levels synchronized to {level}%."}

            if action == "mute":
                if self._set_mute_via_coreaudio(True):
                    return {"status": "success", "message": "System muted via CoreAudio."}
                pyautogui.press("volumemute")
                return {"status": "success", "message": "System muted."}

            if action == "unmute":
                if self._set_mute_via_coreaudio(False):
                    return {"status": "success", "message": "System unmuted via CoreAudio."}
                pyautogui.press("volumemute")
                return {"status": "success", "message": "Mute toggled."}

            if action == "play_pause":
                pyautogui.press("playpause")
                return {"status": "success", "message": "Playback toggled."}

            if action == "next_track":
                pyautogui.press("nexttrack")
                return {"status": "success", "message": "Skipped to the next track."}

            return {"status": "error", "message": f"Unknown media action: {action}"}

        except Exception as exc:
            return {"status": "error", "message": f"Media control failure: {exc}"}


def setup():
    return MediaControlSkill()
