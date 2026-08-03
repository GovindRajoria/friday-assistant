"""Speech to text for the HUD's microphone, as one long-lived model.

The console entry point captures audio itself through `core/listener.py`. The
desktop HUD cannot: it is an Electron renderer, and the microphone it can
reach is the browser's. So the renderer records the utterance and ships the
encoded blob down the existing WebSocket, and this module turns those bytes
into a sentence.

Two properties matter more than anything else here.

**The model is loaded once.** `WhisperModel(...)` is 1.3s of work for
`small.en` from a warm disk cache and considerably more cold; doing it per
utterance would make push-to-talk feel broken no matter which model was
chosen. The server builds one of these at startup and keeps it.

**Decoding happens inside faster-whisper.** `MediaRecorder` produces
WebM/Opus, which is not a format anything here can read directly — but
faster-whisper decodes through PyAV (already a hard dependency of it), so a
`BytesIO` of the raw blob is accepted as-is. Verified on this machine against
a real WebM/Opus blob before any of the renderer side was written; the
alternative was capturing raw PCM through an AudioWorklet and framing WAV by
hand in the renderer, which is a materially larger amount of code.

Model choice is measured, not assumed. On this machine (CPU, int8, warm
cache, an 8.58s utterance):

    model             load    transcribe   x realtime
    base.en           0.8s    0.77s        0.09
    small.en          1.3s    2.08s        0.24
    medium.en         3.3s    8.39s        0.98
    large-v3-turbo    ~30s    11.75s       1.37
    distil-large-v3   ~30s    12.06s       1.41

Push-to-talk means the operator sits and waits for this to finish before
anything happens at all, so anything at or past realtime is unusable however
accurate it is — which rules out all three large models on CPU here. The
default is `small.en`: still a quarter of realtime, and materially better
than `base.en` on accented and noisy speech, which is the case that actually
matters. `audio.stt_model` overrides it for anyone willing to trade the wait.
"""
import io
import threading

from core.config import SETTINGS


def build_vocabulary_prompt(settings) -> str:
    """Words this assistant hears often and general English models do not.

    Whisper's `initial_prompt` biases decoding toward the vocabulary it
    contains. Proper nouns are where a general model reliably fails — the
    assistant's own name, the operator's, and their city — so those go in by
    default, and `audio.stt_vocabulary` carries anything else the operator
    finds themselves repeating.

    Kept deliberately short. The prompt is a bias, not a dictionary: a long
    one starts pulling its own words into the transcript on quiet audio.
    """
    terms = [
        settings["assistant"]["name"],
        settings["assistant"]["wake_word"],
        settings["user"]["name"],
        settings["user"]["location"],
        settings["audio"].get("stt_vocabulary", ""),
    ]
    joined = ", ".join(term.strip() for term in terms if term and term.strip())
    return f"{joined}." if joined else ""


class Transcriber:
    """One loaded Whisper model, callable from any thread."""

    def __init__(self, settings=None):
        self.settings = settings or SETTINGS
        audio = self.settings["audio"]
        self.model_size = audio.get("stt_model", "small.en")
        self.language = audio.get("stt_language") or None
        self.initial_prompt = build_vocabulary_prompt(self.settings) or None
        # faster-whisper makes no thread-safety guarantee about concurrent
        # transcribe() calls on one model, and the server hands each utterance
        # to a fresh worker thread. Serialising them costs nothing real —
        # there is one microphone and one operator — and removes the question.
        self._lock = threading.Lock()

        # Imported here rather than at module scope for the same reason
        # core/llm_client.py defers `ollama`: CI installs no requirements and
        # tests/test_imports_without_runtime_deps.py masks this package
        # outright, so a module-level import would make anything that imports
        # the server uncollectable there.
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            self.model_size,
            device=audio.get("stt_device", "cpu"),
            compute_type=audio.get("stt_compute_type", "int8"),
            # None means "wherever huggingface_hub caches things", which is
            # under the user's home on C:. This machine has 6 GB free there
            # and a large model is 1.5 GB, so the setting exists to point the
            # cache at a roomier drive.
            download_root=audio.get("stt_download_root") or None,
        )

    def transcribe(self, audio: bytes) -> str:
        """Turn one encoded utterance into text. Returns "" for silence.

        Accepts whatever container the caller recorded — WebM/Opus from the
        renderer, WAV from a test — because PyAV underneath does the demuxing.
        """
        if not audio:
            return ""

        with self._lock:
            segments, _info = self.model.transcribe(
                io.BytesIO(audio),
                beam_size=5,
                # Drops silence and room tone before the model ever sees it.
                # Without this, Whisper's well-known failure on near-silent
                # audio is to emit something plausible anyway — and a
                # hallucinated sentence here does not stay a display bug, it
                # becomes a prompt.
                vad_filter=True,
                language=self.language,
                initial_prompt=self.initial_prompt,
                # Each press of the button is a separate utterance. Carrying
                # the previous one in as context is what makes Whisper loop,
                # repeating a phrase until it fills the window.
                condition_on_previous_text=False,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
