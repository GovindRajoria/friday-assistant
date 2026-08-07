# core/speaker.py
import pyttsx3

from core.config import SETTINGS


class FridaySpeaker:
    def __init__(self, settings=None):
        self.settings = settings or SETTINGS
        self.engine = pyttsx3.init()

        # Prefer the second installed voice where one exists; on Windows SAPI
        # that is typically a female voice. Driver voice lists differ across
        # platforms (nsss on macOS, espeak on Linux), so this is best-effort.
        voices = self.engine.getProperty('voices')
        if len(voices) > 1:
            self.engine.setProperty('voice', voices[1].id)

        self._rate = None
        self._apply_rate()
        print("[*] Speaker online.")

    def _apply_rate(self):
        """Re-read the configured rate, so a change is audible without a restart.

        `skills/utility/manage_settings.py` writes the YAML *and* mutates the
        in-memory SETTINGS dict, which is the same object this holds a reference
        to. Reading it per utterance is therefore what makes "speak more slowly"
        take effect on the next sentence rather than on the next launch — and an
        assistant that says "I have slowed down" and then does not is claiming an
        action it did not take.

        Guarded on an actual change: this runs before every utterance and
        setProperty on SAPI is not free.
        """
        rate = self.settings["audio"]["speech_rate"]
        if rate != self._rate:
            self.engine.setProperty('rate', rate)
            self._rate = rate

    def speak(self, text):
        print(f"[FRIDAY] {text}")
        self._apply_rate()

        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except RuntimeError:
            # The engine's run loop is already active; skip audio rather than
            # taking the whole session down.
            pass
