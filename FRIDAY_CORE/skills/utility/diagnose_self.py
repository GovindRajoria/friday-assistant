# skills/utility/diagnose_self.py
"""One question that answers the ten things that have actually gone wrong here.

The list is not invented. Every check below corresponds to a real failure this
project has spent time on: Ollama not running, the model tag absent, C: down to
946 MB during a model download so the download silently failed, the exported
OpenVINO directory missing so vision vanished, a microphone held by another
application, a remote inference host that had gone quiet.

Every one of those presented as strange behaviour and was diagnosed by hand. The
point of this skill is that the next one is a question instead of an
investigation.

Terminal, and every fact comes from a probe rather than from the model. A
diagnostic a model is invited to summarise is a diagnostic that can report a
healthy machine that is not there.
"""
import platform
import shutil
import socket
from urllib.parse import urlparse

from core.config import SETTINGS, vision_model_path

# Enough to matter: a large Whisper model is ~1.5 GB and the exported vision
# model plus a language model dwarf that.
LOW_DISK_GB = 5.0
PROBE_TIMEOUT_SECONDS = 2.0


class DiagnoseSelfSkill:
    def __init__(self):
        self.manifest = {
            "name": "diagnose_self",
            "description": (
                "Checks your own operating environment and reports what is wrong: whether "
                "the language model host is reachable and has the model, free disk space, "
                "whether the vision model files exist, whether a microphone and camera are "
                "present, and which optional packages are missing. Use this when something "
                "is broken or behaving strangely, or when asked if you are working "
                "properly. Its answer is complete — the turn ends when it returns. "
                "This is the broad sweep; for one specific question prefer the narrower "
                "skill: skill_health for which skills failed to load, disk_report for "
                "storage, network_status for connectivity, gpu_status for accelerators, "
                "system_check for a quick CPU and RAM reading."
            ),
            "parameters": [],
            "terminal": True,
        }

    def execute(self, params=None):
        checks = [
            self._llm_host(),
            self._vlm_host(),
            self._disk(),
            self._vision_model(),
            self._stt_model(),
            self._microphone(),
            self._camera(),
            self._packages(),
        ]

        problems = [line for ok, line in checks if not ok]
        fine = [line for ok, line in checks if ok]

        header = ("Everything I check is in order."
                  if not problems else
                  f"{len(problems)} problem(s) found:")
        body = "\n".join(f"  [!] {line}" for line in problems)
        good = "\n".join(f"  [ok] {line}" for line in fine)
        report = "\n".join(part for part in (header, body, "Also checked:" if problems else "", good) if part)

        return {
            "status": "success",
            "message": f"{platform.system()} {platform.release()}.\n{report}",
            "data": {"problems": len(problems), "checks": len(checks)},
        }

    # ---- individual probes ------------------------------------------------

    def _llm_host(self):
        host = SETTINGS["llm"]["host"]
        model = SETTINGS["llm"]["model"]
        reachable = self._reachable(host)
        if not reachable:
            return False, (f"The language model host {host} is not answering. "
                           "Nothing that needs reasoning will work. Start Ollama.")

        tags = self._ollama_tags(host)
        if tags is None:
            return True, f"{host} is reachable (could not list its models)."
        if not any(tag == model or tag.startswith(f"{model}:") for tag in tags):
            return False, (f"{host} is up but does not have '{model}'. "
                           f"It has: {', '.join(sorted(tags)) or 'nothing'}. Run: ollama pull {model}")
        return True, f"{host} is up and has '{model}'."

    def _vlm_host(self):
        if not SETTINGS["vlm"]["enabled"]:
            return True, "Vision-language model is disabled in settings (vlm.enabled)."
        host = SETTINGS["vlm"]["host"]
        model = SETTINGS["vlm"]["model"]
        if not self._reachable(host):
            return False, f"The vision model host {host} is not answering, so screen descriptions will fail."
        tags = self._ollama_tags(host)
        if tags is not None and not any(t == model or t.startswith(f"{model}:") for t in tags):
            return False, f"{host} is up but does not have '{model}'. Run: ollama pull {model}"
        return True, f"Vision model host {host} is up."

    def _disk(self):
        try:
            usage = shutil.disk_usage(str(vision_model_path(SETTINGS).parent))
        except OSError as error:
            return False, f"Could not read disk usage: {error}"
        free_gb = usage.free / 1024 ** 3
        if free_gb < LOW_DISK_GB:
            return False, (f"Only {free_gb:.1f} GB free. A model download needs more than that "
                           "and will fail part-way — this has happened before at 946 MB.")
        return True, f"{free_gb:.1f} GB free on the drive holding the models."

    def _vision_model(self):
        path = vision_model_path(SETTINGS)
        if not path.exists():
            return False, (f"The exported vision model is missing from {path}, so "
                           "scan_environment cannot load and I cannot see the room. "
                           "Regenerate it: python benchmarks/openvino_yolo11_opt.py")
        contents = list(path.glob("*.xml"))
        if not contents:
            return False, f"{path} exists but holds no .xml IR file, so the model cannot be read."
        return True, f"Vision model present at {path.name}."

    def _stt_model(self):
        audio = SETTINGS["audio"]
        if not audio.get("stt_enabled", True):
            return True, "Speech recognition is disabled in settings (audio.stt_enabled)."
        return True, (f"Speech recognition set to '{audio.get('stt_model')}' on "
                      f"{audio.get('stt_device')} — downloaded on first use.")

    def _microphone(self):
        """Only the console listener's microphone. The HUD's is not this device.

        Worth being exact about, because the obvious wording here is wrong: the
        desktop window records through the browser's getUserMedia and posts the
        audio to the server, so it needs no PyAudio and no local input device
        enumeration at all. Reporting "voice input will not work" on a missing
        PyAudio would send someone debugging a microphone that is fine.
        """
        try:
            import speech_recognition as sr
        except ImportError:
            return True, "Cannot check the console microphone (SpeechRecognition not installed)."
        try:
            names = sr.Microphone.list_microphone_names()
        except Exception as error:                                    # noqa: BLE001
            return False, (f"The console listener cannot open a microphone: {error}. "
                           "The desktop window is unaffected — it records through the "
                           "browser and needs no local audio device.")
        if not names:
            return False, ("No microphone is visible to the console listener. The desktop "
                           "window records through the browser and is unaffected.")
        return True, f"{len(names)} audio input device(s) visible to the console listener."

    def _camera(self):
        try:
            import cv2
        except ImportError:
            return True, "Cannot check the camera (opencv not installed)."
        index = SETTINGS["vision"]["camera_index"]
        capture = None
        try:
            capture = cv2.VideoCapture(index)
            if not capture.isOpened():
                return False, (f"Camera {index} could not be opened — it may be missing, "
                               "disabled in privacy settings, or held by another application.")
            return True, f"Camera {index} opens."
        except Exception as error:                                    # noqa: BLE001
            return False, f"Camera {index} check failed: {error}"
        finally:
            if capture is not None:
                capture.release()

    def _packages(self):
        """The optional imports whose absence silently removes a skill."""
        import importlib.util

        optional = {
            "pypdf": "read_document cannot open PDFs",
            "openpyxl": "read_spreadsheet cannot open .xlsx",
            "icalendar": "calendar cannot parse .ics files",
            "mss": "screenshot and screen awareness cannot capture",
            "psutil": "process and disk reporting is unavailable",
        }
        missing = [f"{name} ({why})" for name, why in optional.items()
                   if importlib.util.find_spec(name) is None]
        if missing:
            return False, "Missing optional packages: " + "; ".join(missing)
        return True, f"All {len(optional)} optional packages present."

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _reachable(host: str) -> bool:
        """A TCP connect, not an HTTP request — cheap and enough to answer 'is it there'."""
        parsed = urlparse(host if "//" in host else f"http://{host}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((parsed.hostname, port), timeout=PROBE_TIMEOUT_SECONDS):
                return True
        except OSError:
            return False

    @staticmethod
    def _ollama_tags(host: str):
        """Model tags the host has, or None if they could not be listed."""
        try:
            import requests

            response = requests.get(f"{host.rstrip('/')}/api/tags", timeout=PROBE_TIMEOUT_SECONDS)
            response.raise_for_status()
            return [entry.get("name", "") for entry in response.json().get("models", [])]
        except Exception:                                             # noqa: BLE001
            return None


def setup():
    return DiagnoseSelfSkill()
