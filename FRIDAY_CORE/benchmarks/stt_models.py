"""Time the speech-recognition candidates on this machine, on your own voice.

Model choice for speech is not portable advice. It depends on the CPU, and it
depends far more on the voice: a model that is flawless on the clean American
English of a benchmark corpus can mangle an accent it rarely heard in
training. The numbers in core/transcriber.py were measured on one machine with
synthesised speech, which settles the latency question and settles nothing
about accuracy. This script is how you settle the second one for yourself.

    python benchmarks/stt_models.py --record
        Records you saying a sentence, then transcribes that recording with
        every candidate so you can read the transcripts side by side.

    python benchmarks/stt_models.py --clip path\\to\\utterance.wav
        Same, against a recording you already have.

    python benchmarks/stt_models.py --models base.en,small.en
        Narrower list. The default list downloads about 4 GB the first time.

The measurement that matters is the transcribe column against the length of
your clip. Push-to-talk means you sit and wait for it, so a model at or past
realtime is unusable however good its transcript is.
"""
import argparse
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import SETTINGS  # noqa: E402 — after the sys.path fix above
from core.transcriber import build_vocabulary_prompt  # noqa: E402

DEFAULT_MODELS = ["base.en", "small.en", "medium.en", "distil-large-v3", "large-v3-turbo"]

PROMPT_SENTENCE = (
    "Friday, what is the weather here today, and read me the top headlines "
    "before you set a reminder to check the build in twenty minutes."
)


def record(path, seconds=12):
    """Capture one utterance from the default microphone."""
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    recognizer.pause_threshold = SETTINGS["audio"]["pause_threshold"]
    print("Read this out loud, in your normal speaking voice:\n")
    print(f"    {PROMPT_SENTENCE}\n")
    input("Press Enter when you are ready, then start speaking. ")
    with sr.Microphone(device_index=SETTINGS["audio"]["input_device_index"]) as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("[*] Listening...")
        audio = recognizer.listen(source, phrase_time_limit=seconds)
    Path(path).write_bytes(audio.get_wav_data())
    print(f"[*] Saved {path}")
    return path


def clip_seconds(path):
    with wave.open(str(path)) as handle:
        return handle.getnframes() / handle.getframerate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="record a fresh clip from the microphone")
    parser.add_argument("--clip", default="utterance.wav", help="WAV file to transcribe")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    args = parser.parse_args()

    if args.record:
        record(args.clip)
    if not Path(args.clip).exists():
        parser.error(f"{args.clip} does not exist — pass --record to make one")

    from faster_whisper import WhisperModel

    audio_config = SETTINGS["audio"]
    initial_prompt = build_vocabulary_prompt(SETTINGS) or None
    seconds = clip_seconds(args.clip)
    print(f"\nclip: {args.clip}, {seconds:.2f}s")
    print(f"vocabulary bias: {initial_prompt or '(none)'}\n")

    for name in [entry.strip() for entry in args.models.split(",") if entry.strip()]:
        try:
            started = time.perf_counter()
            model = WhisperModel(
                name,
                device=audio_config.get("stt_device", "cpu"),
                compute_type=audio_config.get("stt_compute_type", "int8"),
                download_root=audio_config.get("stt_download_root") or None,
            )
            load = time.perf_counter() - started
        except Exception as error:  # noqa: BLE001 — one unavailable model must not end the run
            print(f"{name:<18} unavailable: {error}\n")
            continue

        started = time.perf_counter()
        segments, _info = model.transcribe(
            args.clip, beam_size=5, vad_filter=True,
            language=audio_config.get("stt_language") or None,
            initial_prompt=initial_prompt, condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        elapsed = time.perf_counter() - started
        del model

        print(f"{name:<18} load {load:5.1f}s  transcribe {elapsed:5.2f}s  ({elapsed / seconds:.2f}x realtime)")
        print(f"                   {text}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
