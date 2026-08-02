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
    },
    "vision": {
        "camera_index": 0,
        "model_dir": "yolo11n_openvino_model",
        "confidence": 0.5,
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
