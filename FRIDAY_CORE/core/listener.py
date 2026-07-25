# core/listener.py
import io
import platform
import sys

import speech_recognition as sr
from faster_whisper import WhisperModel

from core.config import SETTINGS

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    import msvcrt
else:
    import select


def _keypress_waiting():
    """True if the user has hit a key, meaning they want to type instead of speak.

    Windows exposes this through msvcrt; elsewhere we poll stdin with a
    zero-length select, which behaves the same way for an interactive terminal.
    """
    if IS_WINDOWS:
        return msvcrt.kbhit()
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (OSError, ValueError):
        # No usable stdin (piped input, service context): voice only.
        return False


def _consume_keypress():
    """Discard the buffered keystroke so it does not leak into input()."""
    if IS_WINDOWS:
        msvcrt.getch()


class FridayListener:
    def __init__(self, model_size="base.en", wake_word=None, settings=None):
        self.settings = settings or SETTINGS
        self.wake_word = (wake_word or self.settings["assistant"]["wake_word"]).lower()
        audio = self.settings["audio"]
        self.device_index = audio["input_device_index"]
        self.phrase_time_limit = audio["phrase_time_limit"]

        print(f"[*] Loading Whisper STT Model ({model_size}) on CPU...")
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.recognizer = sr.Recognizer()

        # Wait this long after speech stops before processing, so half-finished
        # sentences are not cut off mid-thought.
        self.recognizer.pause_threshold = audio["pause_threshold"]

        print("[*] Listener online and ready.")

    def listen(self, require_wake_word=True):
        print("[*] Listening (Press any key to TYPE instead)...")

        while True:
            # 1. Hybrid input: a keypress switches to typed commands.
            if _keypress_waiting():
                _consume_keypress()
                return input("\n[TYPE YOUR COMMAND]: ")

            # 2. Otherwise capture from the microphone.
            try:
                # device_index=None lets speech_recognition pick the system default.
                with sr.Microphone(device_index=self.device_index) as source:
                    # Short timeout so keypresses are still checked frequently.
                    audio = self.recognizer.listen(
                        source, timeout=1, phrase_time_limit=self.phrase_time_limit
                    )

                    wav_data = io.BytesIO(audio.get_wav_data())
                    segments, _info = self.model.transcribe(wav_data, beam_size=5)
                    transcription = "".join(s.text for s in segments).strip().lower()

                    if not transcription:
                        continue  # Silence, loop back and check for keypresses.

                    if not require_wake_word:
                        return transcription

                    if self.wake_word in transcription:
                        # Strip the wake word so the model receives only the command.
                        command = transcription.replace(self.wake_word, "").strip()
                        return command if command else "yes?"
                    # Wake word absent, keep waiting.

            except sr.WaitTimeoutError:
                continue  # Normal silence timeout.
            except Exception as exc:
                print(f"[-] Microphone error: {exc}")
                return None


def setup():
    return FridayListener()
