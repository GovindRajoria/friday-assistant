# skills/os_control/media_control.py
"""System audio control.

Volume and mute handling is inherently platform-specific. The Windows path uses
the CoreAudio COM interface for anything with a target state — a level to reach,
muted or unmuted — and the OS media keys only where a blind toggle is genuinely
what was asked for: playback and track skipping. The module imports those
dependencies lazily, so on other platforms the skill still loads and reports
cleanly instead of failing at import time and disappearing from the registry.

**`set_volume` used to be a fifty-keypress dance.** It pressed `volumemute`
twice "to wake the driver", then `volumedown` fifty times to reach zero, then
`volumeup` up to fifty times to climb back to the requested level — and finished
by playing a 1000 Hz beep at the operator. A hundred keystrokes take a visible
moment, land on whatever window has focus, and leave the volume wherever the
steps happened to land, since a 2% assumption about the key's increment is not
guaranteed. `_apply_mute` already proved the CoreAudio path works on this
machine, and it sets a level exactly, instantly, with no keystrokes at all.
Where that interface is unreachable, an absolute level now reports honestly
rather than approximating one by feel — the same choice `_apply_mute` makes about
a mute state it cannot read.
"""
import platform

IS_WINDOWS = platform.system() == "Windows"

# How much "louder"/"quieter" moves when nobody says how much.
RELATIVE_STEP_PERCENT = 10

# Spoken ways of asking for the same four things. The model picks an action name
# from the description, but it also paraphrases, and "louder" arriving as an
# unknown action was a failure the operator would read as the skill being broken.
ALIASES = {
    "louder": "volume_up", "up": "volume_up", "raise": "volume_up",
    "increase": "volume_up", "turn_up": "volume_up",
    "quieter": "volume_down", "down": "volume_down", "lower": "volume_down",
    "softer": "volume_down", "decrease": "volume_down", "turn_down": "volume_down",
    "silence": "mute", "silent": "mute", "unsilence": "unmute",
    "pause": "play_pause", "play": "play_pause", "resume": "play_pause",
    "skip": "next_track", "next": "next_track",
}


class MediaControlSkill:
    def __init__(self):
        self.manifest = {
            "name": "media_control",
            # Rewritten after a measurement: all three volume cases in
            # tools/routing_cases.yaml routed to `manage_settings` instead, whose
            # own description advertised "whether you mute audio". A one-line
            # description with none of the vocabulary anybody actually uses —
            # loud, quiet, sound, speakers — cannot win that comparison. The last
            # sentence is the distinction the model kept getting wrong: this skill
            # is the machine's audio, not the assistant's voice.
            "description": (
                "Changes how loud the machine's speakers are, and controls whatever is "
                "playing on them. Use this for: turn the volume up or down, make it louder "
                "or quieter or softer, set the volume to a percentage, what is the volume, "
                "mute or unmute the audio, silence the sound, pause or resume playback, skip "
                "to the next track. This is the system's audio output — the speakers. It is "
                "not how fast or in what voice the assistant itself speaks, which is "
                "voice_control."
            ),
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

    def _volume_percent(self):
        """Current output level as 0-100, or None when it cannot be read."""
        try:
            volume = self._endpoint_volume()
            if volume is None:
                return None
            return round(volume.GetMasterVolumeLevelScalar() * 100)
        except Exception:
            return None

    def _set_volume_percent(self, percent):
        """Set the output level exactly. Returns the level reached, or None.

        Scalar rather than decibels: `SetMasterVolumeLevelScalar` takes 0.0-1.0 on
        the same curve the Windows volume slider uses, so "thirty percent" means
        what the operator sees when they look at the tray. The decibel form is
        linear in power and 30% of that range is nearly inaudible.

        Read back afterwards rather than trusting the call, for the same reason
        _set_mute_via_coreaudio does: the point of this path over the media keys
        is that the resulting state is known.
        """
        try:
            volume = self._endpoint_volume()
            if volume is None:
                return None
            volume.SetMasterVolumeLevelScalar(percent / 100, None)
            return round(volume.GetMasterVolumeLevelScalar() * 100)
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
        action = str(params.get("action", "set_volume")).strip().lower().replace(" ", "_")
        action = ALIASES.get(action, action)
        level = params.get("level", None)

        if not IS_WINDOWS:
            return {
                "status": "error",
                "message": f"Audio control is only wired up for Windows; this host is {platform.system()}.",
            }

        try:
            import pyautogui

            if action == "get_volume":
                current = self._volume_percent()
                if current is None:
                    return {"status": "error", "message": "I could not read the system volume."}
                return {"status": "success", "message": f"The volume is at {current}%."}

            if action == "set_volume":
                try:
                    wanted = max(0, min(100, int(float(level))))
                except (TypeError, ValueError):
                    return {"status": "error", "message": f"'{level}' is not a valid volume level."}

                reached = self._set_volume_percent(wanted)
                if reached is None:
                    # Honest failure rather than fifty keypresses and a guess.
                    return {
                        "status": "error",
                        "message": ("I could not set the volume: the system audio interface is "
                                    "unreachable, and the media keys only step by a fixed amount, "
                                    "so I cannot reach a specific level with them."),
                    }
                return {"status": "success", "message": f"Volume set to {reached}%."}

            if action in ("volume_up", "volume_down"):
                try:
                    step = abs(int(float(level))) if level is not None else RELATIVE_STEP_PERCENT
                except (TypeError, ValueError):
                    step = RELATIVE_STEP_PERCENT
                current = self._volume_percent()
                if current is not None:
                    wanted = current + (step if action == "volume_up" else -step)
                    reached = self._set_volume_percent(max(0, min(100, wanted)))
                    if reached is not None:
                        return {"status": "success",
                                "message": f"Volume {'up' if action == 'volume_up' else 'down'} "
                                           f"to {reached}%."}
                # A relative move is the one case the media keys can honestly do
                # blind: there is no target state to get wrong, only a direction.
                key = "volumeup" if action == "volume_up" else "volumedown"
                presses = max(1, round(step / 2))
                for _ in range(presses):
                    pyautogui.press(key)
                return {"status": "success",
                        "message": f"Volume stepped {'up' if action == 'volume_up' else 'down'} "
                                   "with the media keys; the exact level is unreadable on this host."}

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
