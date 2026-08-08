# skills/utility/voice_control.py
"""Change how the assistant itself speaks, on request, out loud.

A voice-first assistant that cannot be told to slow down is a gap the operator
hits within a minute of using it. `manage_settings` could already write
`audio.speech_rate`, but only as one key among fourteen, described in a sentence
about YAML — and it is marked destructive, so "speak more slowly" earned a
confirmation prompt before it would do anything. Being asked to approve a change
to the speaking rate is the wrong shape of interaction for an instruction shouted
across a room.

**It takes effect immediately, which is a property of two things meeting.**
`core/speaker.py` re-reads `audio.speech_rate` before every utterance, and this
skill mutates the in-memory SETTINGS dict as well as the YAML, so the next
sentence is spoken at the new rate. That matters more than convenience: an
assistant that says "I have slowed down" and then does not has claimed an action
it did not take, which is the specific failure this project treats as worse than
an error message.

Not destructive. It changes how the assistant sounds and nothing else — no file
the operator relies on, no state anything else reads, and it is trivially
reversible by asking again.
"""
from core.config import SETTINGS

# Words per minute. The pyttsx3/SAPI default is 200; this project ships 175
# because 200 is noticeably hurried for a long answer.
SLOWEST = 90
FASTEST = 320
# How far "faster" and "slower" move. 25 wpm is about a seventh of the default —
# clearly audible in one step, so a single request feels like it did something,
# without three requests reaching an extreme.
STEP = 25

READABLE = {
    "rate": "audio.speech_rate",
    "enabled": "server.speak",
}


class VoiceControlSkill:
    def __init__(self):
        self.manifest = {
            "name": "voice_control",
            "description": (
                "Changes how the assistant's own voice sounds and behaves. Use this for: "
                "speak more slowly, slow down, you are talking too fast, speed up, talk "
                "faster, how fast are you speaking, stop talking out loud, be quiet from now "
                "on, start speaking again, use a different voice, which voices are available. "
                "This is the assistant's speech — not the volume of the machine's speakers, "
                "which is media_control. Its answer is complete — the turn ends there."
            ),
            "parameters": ["action", "value"],
            # Ends the turn: the answer is the confirmation, and there is nothing
            # for the model to reason about afterwards. Without this it would
            # loop back into reason with an observation and often narrate the
            # change a second time.
            "terminal": True,
        }

    def _write(self, key, value):
        """Persist through manage_settings, so there is one writer, not two.

        That skill owns the allowlist, the per-key coercion and the
        comment-preserving line edit of settings.yaml. Re-implementing any of
        that here would be a second place for the file format to be got wrong,
        and the first thing to drift.
        """
        from skills.utility.manage_settings import ManageSettingsSkill

        return ManageSettingsSkill().execute({"action": "set", "key": key, "value": str(value)})

    def _voices(self):
        """(index, name) for each installed voice, or None if unreadable.

        A fresh engine rather than the running one: the speech thread's engine is
        COM and thread-affine (server/app.py:_SpeechThread), so touching it from
        here is the deadlock that class exists to avoid. Listing voices is a
        read-only query and a second short-lived engine is safe.
        """
        try:
            import pyttsx3

            engine = pyttsx3.init()
            return [(index, getattr(voice, "name", voice.id))
                    for index, voice in enumerate(engine.getProperty("voices"))]
        except Exception:
            return None

    def _set_rate(self, wanted):
        wanted = max(SLOWEST, min(FASTEST, int(wanted)))
        result = self._write("audio.speech_rate", wanted)
        if result.get("status") != "success":
            return result
        # Said at the new rate, because the speaker re-reads the setting before
        # every utterance — which is the only honest way to answer "is that
        # better?" with a demonstration rather than a claim.
        return {"status": "success",
                "message": f"I am now speaking at {wanted} words a minute. Is this better?"}

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action", "")).strip().lower().replace(" ", "_")
        value = params.get("value")

        current = int(SETTINGS["audio"].get("speech_rate", 175))

        if action in ("slower", "slow_down", "speak_slower", "too_fast"):
            return self._set_rate(current - STEP)
        if action in ("faster", "speed_up", "speak_faster", "too_slow"):
            return self._set_rate(current + STEP)
        if action in ("set_rate", "rate"):
            try:
                return self._set_rate(int(float(value)))
            except (TypeError, ValueError):
                return {"status": "error",
                        "message": f"'{value}' is not a speaking rate. Give me words per minute, "
                                   f"between {SLOWEST} and {FASTEST}."}
        if action in ("get_rate", "status", ""):
            spoken = "aloud" if SETTINGS["server"].get("speak") else "silently"
            return {"status": "success",
                    "message": f"I speak at {current} words a minute, and I am currently "
                               f"answering {spoken}."}
        if action in ("mute", "be_quiet", "stop_speaking", "silence"):
            # server.speak is read when the process starts, so this one cannot
            # take effect until then — said plainly rather than implied, since the
            # operator is about to test it by talking to it.
            result = self._write("server.speak", False)
            if result.get("status") != "success":
                return result
            return {"status": "success",
                    "message": "I will stop answering out loud from the next restart. "
                               "To stop me mid-sentence right now, just say stop."}
        if action in ("unmute", "speak", "start_speaking"):
            result = self._write("server.speak", True)
            if result.get("status") != "success":
                return result
            return {"status": "success", "message": "I will answer out loud from the next restart."}

        if action in ("list_voices", "voices", "which_voices"):
            voices = self._voices()
            if not voices:
                return {"status": "error", "message": "I could not read the installed voices."}
            listed = ", ".join(f"{index}: {name}" for index, name in voices)
            return {"status": "success", "message": f"The installed voices are {listed}."}

        if action in ("set_voice", "voice", "change_voice"):
            # Deliberately reports rather than pretends. Choosing the voice lives
            # in core/speaker.py's constructor, which picks the second installed
            # voice and has no setting behind it — so there is nothing to write,
            # and claiming otherwise would be exactly the false success this
            # module's docstring objects to.
            voices = self._voices()
            listed = (", ".join(f"{index}: {name}" for index, name in voices)
                      if voices else "unreadable")
            return {"status": "error",
                    "message": ("I cannot change my voice yet — which one I use is fixed in code "
                                f"rather than in settings. The voices installed here are: {listed}. "
                                "Speaking rate I can change.")}

        return {"status": "error",
                "message": f"I do not know how to '{action}' my voice. I can speak faster or "
                           "slower, set a rate in words per minute, report the rate, stop or "
                           "start answering aloud, and list the installed voices."}


def setup():
    return VoiceControlSkill()
