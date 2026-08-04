# skills/os_control/media_control.py
"""System audio control.

Volume and mute handling is inherently platform-specific. The Windows path
drives the OS media keys for volume and playback, and the CoreAudio COM
interface for mute — that way round because mute has a target state and the
media key only toggles. The module imports those dependencies lazily, so on
other platforms the skill still loads and reports cleanly instead of failing at
import time and disappearing from the skill registry.
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
            "description": "Controls system audio and playback. Parameters: 'action' (set_volume, mute, unmute, play_pause, next_track), 'level' (0-100 for set_volume).",
            "parameters": ["action", "level"]
        }

    def _endpoint_volume(self):
        """The CoreAudio endpoint volume interface, or None.

        pycaw's API surface has shifted between releases and requirements.txt
        does not pin it, so this handles both shapes it has taken. Current
        pycaw (20251023 here) returns a wrapped ``AudioDevice`` from
        GetSpeakers() with the interface already built on ``.EndpointVolume``;
        older releases returned the raw ``IMMDevice`` pointer, which has to be
        Activate()d. The previous version of this method looked only for
        ``Activate`` on the wrapper, never found it, and reported failure on
        every call — which pushed every mute onto the media-key fallback.
        """
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        device = AudioUtilities.GetSpeakers()

        ready = getattr(device, "EndpointVolume", None)
        if ready is not None:
            return ready

        # Either the raw pointer itself, or the one the wrapper holds.
        for candidate in (device, getattr(device, "_dev", None)):
            activate = getattr(candidate, "Activate", None)
            if activate:
                interface = activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                return interface.QueryInterface(IAudioEndpointVolume)
        return None

    def _set_mute_via_coreaudio(self, muted):
        """Set — not toggle — the system mute state. True when it took.

        Verified against GetMute() afterwards rather than trusting the call,
        because the whole point of this path over the media key is that the
        resulting state is known.
        """
        try:
            volume = self._endpoint_volume()
            if volume is None:
                return False
            volume.SetMute(1 if muted else 0, None)
            return bool(volume.GetMute()) == bool(muted)
        except Exception:
            return False

    def _is_muted_via_coreaudio(self):
        """Current mute state, or None when it cannot be read."""
        try:
            volume = self._endpoint_volume()
            return None if volume is None else bool(volume.GetMute())
        except Exception:
            return None

    def _apply_mute(self, muted, pyautogui):
        """Reach a known mute state, or say plainly that it could not.

        The media key *toggles*, so pressing it blind cannot implement "mute":
        on an already-muted machine it unmutes. That is how the anomaly guard's
        latch and the real audio state used to drift apart. So the key is only
        pressed when the current state is known and wrong, and a state that
        cannot be read is reported as a failure rather than as success.
        """
        wanted = "muted" if muted else "unmuted"

        if self._set_mute_via_coreaudio(muted):
            return {"status": "success", "message": f"System {wanted}."}

        current = self._is_muted_via_coreaudio()
        if current is None:
            return {
                "status": "error",
                "message": (f"I could not {'mute' if muted else 'unmute'} the audio: the system "
                            "mute state is unreadable, and the media key only toggles, so pressing "
                            "it could have done the opposite."),
            }
        if current == bool(muted):
            return {"status": "success", "message": f"System was already {wanted}."}

        pyautogui.press("volumemute")
        confirmed = self._is_muted_via_coreaudio()
        if confirmed == bool(muted):
            return {"status": "success", "message": f"System {wanted} via the media key."}
        return {"status": "error", "message": f"The media key did not leave the system {wanted}."}

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
            import winsound

            import pyautogui

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

            if action in ("mute", "unmute"):
                return self._apply_mute(action == "mute", pyautogui)

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
