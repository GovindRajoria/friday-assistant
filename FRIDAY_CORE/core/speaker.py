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

        self.engine.setProperty('rate', self.settings["audio"]["speech_rate"])
        print("[*] Speaker online.")

    def speak(self, text):
        print(f"[FRIDAY] {text}")

        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except RuntimeError:
            # The engine's run loop is already active; skip audio rather than
            # taking the whole session down.
            pass
