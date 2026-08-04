# core/config.py
"""Runtime configuration loader.

Settings live in ``config/settings.yaml``, which is not tracked in version
control because it holds the operator's personal details. On a fresh checkout
that file is absent, so values fall back to ``config/settings.example.yaml``
and then to the built-in defaults below. Copy the example file to
``settings.yaml`` and edit it to personalise an install.
"""
import copy
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is declared in requirements.txt
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

DEFAULTS = {
    "assistant": {
        "name": "FRIDAY",
        "wake_word": "friday",
        "address_user_as": "Sir",
    },
    "user": {
        "name": "the operator",
        "location": "",
        "interests": "",
    },
    "llm": {
        "model": "llama3.1",
        "host": "http://127.0.0.1:11434",   # point at a Pi 5 / Intel box to offload
        # Now a real bound on the whole chain, not just on re-sampling a
        # malformed reply. 5 was sized for the latter; a proactive agent that
        # scans, checks system state and then answers spends that before it
        # starts, so the ceiling is raised to match what it now measures.
        "max_react_steps": 12,
        "history_length": 10,
        "temperature": 0.1,                  # routing wants determinism, not flair
    },
    "vlm": {
        "enabled": False,
        "backend": "ollama",                 # "ollama" | "openvino"
        "model": "moondream",
        "host": "http://127.0.0.1:11434",    # may differ from llm.host
    },
    "screen": {
        "enabled": False,
        # Measured on this machine: describe pass median 0.19s at 1280px
        # (0.21s at 768px — no faster, and 768px hallucinated a running
        # program that was not running, 1280px did not), screen grab alone
        # 28ms. Cheap enough to poll fairly often; the change gate below
        # means an idle desktop still only pays the grab+hash between real
        # changes. The 4.86s worst case seen while benchmarking only showed
        # up when llama3.1 was busy at the same time, which
        # vision/watcher.py avoids by skipping a cycle outright while a
        # turn is in flight — that is the fix for contention, not a longer
        # interval here.
        "interval_seconds": 5,
        "change_threshold": 6,               # perceptual-hash hamming distance
        "monitor": 1,
        "max_width": 1280,                   # 768px measured the same speed with worse quality; see above
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8756,
        # Off switch for TTS while iterating on the server. Phase 2/3
        # development means restarting this process constantly; a machine
        # that speaks on every test run is exactly the kind of thing that
        # gets disabled permanently rather than tolerated.
        "speak": True,
    },
    "audio": {
        # None lets speech_recognition pick the system default input device.
        "input_device_index": None,
        "pause_threshold": 2.0,
        "phrase_time_limit": 10,
        "speech_rate": 175,
        # Speech recognition, shared by the console listener and the HUD's
        # push-to-talk button so both hear the same way. Measured on this
        # machine over an 8.58s utterance, CPU int8, warm cache: base.en
        # 0.77s, small.en 2.08s, medium.en 8.39s, large-v3-turbo 11.75s,
        # distil-large-v3 12.06s. Push-to-talk makes the operator wait for
        # this before anything happens, so anything near realtime is out —
        # small.en is a quarter of realtime and clearly better than base.en
        # on accented speech. See core/transcriber.py for the full table.
        # Off switch for the HUD's microphone. False also means the model is
        # never loaded, so nothing is held in memory for a feature nobody uses.
        "stt_enabled": True,
        "stt_model": "small.en",
        "stt_device": "cpu",
        "stt_compute_type": "int8",
        # Where the model weights are cached. None means huggingface_hub's
        # default under the user's home; point it at another drive when C: is
        # tight, since a large model is ~1.5 GB.
        "stt_download_root": None,
        # None lets Whisper detect. The ".en" models are English-only and
        # ignore it; set it for a multilingual model to stop it switching
        # language mid-sentence on an accent.
        "stt_language": None,
        # Extra proper nouns to bias recognition toward — colleagues, product
        # names, anything a general English model keeps getting wrong. The
        # assistant's name, the operator's name and their location are added
        # automatically. Keep it short: this is a bias, not a dictionary.
        "stt_vocabulary": "",
    },
    "vision": {
        "camera_index": 0,
        "model_dir": "yolo11n_openvino_model",
        "confidence": 0.5,
    },
    "proactive": {
        # The only part of this assistant that speaks without being spoken
        # to, so it is opt-in. Everything below is inert while this is False.
        "enabled": False,
        # A daily briefing — weather plus headlines — composed in Python from
        # a fixed read-only skill list, never by a free reasoning turn. See
        # core/briefing.py for why that distinction is load-bearing.
        "briefing_enabled": False,
        "briefing_time": "08:00",
        # Quiet hours gate the SPEECH, not the event. A briefing at 03:00
        # should not wake the house; it should still be waiting on screen.
        # Wraps past midnight, which is the normal case.
        "quiet_start": "22:00",
        "quiet_end": "07:00",
    },
    "privacy": {
        # The camera anomaly rule — more than one person in frame, or the
        # workstation out of frame — used to mute system audio unconditionally.
        # It was a defensible default and it was never the operator's choice,
        # so the detection stays and the intervention is now opt-in.
        #
        # Precedence, because the second name invites the wrong reading:
        #   auto_mute True                    -> mute, and say so
        #   auto_mute False, announce_only T  -> say so, touch nothing (default)
        #   auto_mute False, announce_only F  -> the guard is silent entirely
        # announce_only is only consulted when auto_mute is False; it is not a
        # second off switch for the muting.
        "auto_mute": False,
        "announce_only": True,
    },
    "filesystem": {
        # skills/os_control/manage_files.py refuses to touch anything outside
        # these directories. This is the layer beneath the confirmation gate,
        # not a replacement for it: confirmation stops a human from
        # rubber-stamping a bad request, the allowlist stops a confused or
        # adversarial model from being able to *propose* one against
        # C:\Windows in the first place. Defaults to a dedicated workspace
        # under the user's home rather than the whole home directory — the
        # home directory holds Documents, Downloads and .ssh, none of which
        # should be in scope by default.
        "allowed_roots": [str(Path.home() / "FridayWorkspace")],
    },
}


def _deep_merge(base, override):
    """Recursively overlay ``override`` onto a copy of ``base``."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings():
    """Return the merged settings dictionary.

    Precedence: ``settings.yaml`` > ``settings.example.yaml`` > ``DEFAULTS``.
    """
    settings = DEFAULTS
    if yaml is None:
        return settings

    for filename in ("settings.example.yaml", "settings.yaml"):
        path = CONFIG_DIR / filename
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
            if loaded:
                settings = _deep_merge(settings, loaded)
        except Exception as exc:
            print(f"[-] Could not read {path.name}: {exc}")

    return settings


def vision_model_path(settings):
    """Absolute path to the exported OpenVINO model directory."""
    return PROJECT_ROOT / settings["vision"]["model_dir"]


SETTINGS = load_settings()
