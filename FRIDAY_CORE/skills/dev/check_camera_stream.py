# skills/dev/check_camera_stream.py
"""Point at an RTSP URL, grab one frame, report whether it is alive.

This is the production domain — hundreds of cameras across five sites — and the
question that actually gets asked at 8am is "is camera 12 up", not "monitor camera
12 forever". So this deliberately does the small version: connect, take one frame,
say what happened, disconnect. No monitoring loop, no state, no schedule.

Reporting *why* it failed is most of the value, because the three failure modes need
three different people. A refused connection is a network or credentials problem; a
connection that opens but yields no frame is usually a codec or bandwidth problem;
a timeout is neither. Collapsing those into "camera down" is what makes an outage
take an hour.

Optionally runs detection on the frame it got, reusing the exported model, so "is it
up" can become "it is up and there are two people in front of it" without a second
skill. Off unless asked, because loading the model costs more than the connection
test does.
"""
import socket
import time
from urllib.parse import urlparse

# A stream that has not produced a frame in this long is not going to.
OPEN_TIMEOUT_SECONDS = 12
# The TCP pre-flight below. Short, because this only answers "is anything
# listening" — a camera on a LAN either accepts immediately or is not there.
CONNECT_TIMEOUT_SECONDS = 3
ALLOWED_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")
DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}


class CheckCameraStreamSkill:
    def __init__(self):
        self.manifest = {
            "name": "check_camera_stream",
            "description": (
                "Connects to a network camera stream by URL, grabs a single frame, and "
                "reports whether the camera is alive — with the reason if it is not, "
                "distinguishing a refused connection from a stream that opens but sends no "
                "video. Parameters: 'url' (an rtsp:// or http:// stream) and optionally "
                "'detect' set to yes to also report what objects are in the frame. Use "
                "scan_environment for this computer's own webcam instead."
            ),
            "parameters": ["url", "detect"],
        }

    def execute(self, params=None):
        params = params or {}
        url = str(params.get("url") or "").strip()
        if not url:
            return {"status": "error", "message": "I need the stream URL of the camera to check."}
        if not url.lower().startswith(ALLOWED_SCHEMES):
            return {
                "status": "error",
                "message": (f"'{url}' is not a stream URL I recognise. "
                            f"It should start with one of: {', '.join(ALLOWED_SCHEMES)}."),
            }

        # TCP pre-flight before OpenCV gets involved. Measured 2026-08-04: an
        # unroutable address took OpenCV **36 seconds** to give up, and neither
        # CAP_PROP_OPEN_TIMEOUT_MSEC nor CAP_PROP_READ_TIMEOUT_MSEC shortened it
        # on this build. "Is camera 12 up" has to answer in seconds to be worth
        # asking, and a refused TCP connection is also a *better* diagnosis than
        # anything OpenCV reports — it distinguishes "nothing is listening there"
        # from "the stream is broken".
        reachable, detail, elapsed = self._tcp_probe(url)
        if not reachable:
            return {
                "status": "error",
                "message": (f"Nothing accepted a connection at {self._safe(url)} "
                            f"({detail}, after {elapsed:.1f}s). The camera was never reached, so "
                            "this is an address, network or firewall problem rather than a "
                            "camera fault."),
                "data": {"alive": False, "stage": "connect", "seconds": round(elapsed, 1)},
            }

        try:
            import cv2
        except ImportError as error:
            return {"status": "error", "message": f"Checking a stream needs opencv: {error}"}

        capture = None
        started = time.time()
        try:
            capture = cv2.VideoCapture(url)
            # FFMPEG's own timeout is measured in microseconds and is the only
            # thing that stops an unreachable host hanging for a minute or more.
            try:
                capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_SECONDS * 1000)
                capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, OPEN_TIMEOUT_SECONDS * 1000)
            except Exception:                                         # noqa: BLE001
                pass          # older OpenCV builds do not expose these properties

            if not capture.isOpened():
                elapsed = time.time() - started
                return {
                    "status": "error",
                    "message": (f"The stream at {self._safe(url)} refused the connection after "
                                f"{elapsed:.1f}s. That is a network, address or credentials "
                                "problem rather than a camera fault — the camera was never reached."),
                    "data": {"alive": False, "stage": "connect", "seconds": round(elapsed, 1)},
                }

            ok, frame = capture.read()
            elapsed = time.time() - started
            if not ok or frame is None:
                return {
                    "status": "error",
                    "message": (f"The stream at {self._safe(url)} opened but sent no usable frame "
                                f"in {elapsed:.1f}s. The camera is reachable and something is "
                                "wrong downstream of that — usually a codec the client cannot "
                                "decode, or not enough bandwidth to carry the stream."),
                    "data": {"alive": False, "stage": "read", "seconds": round(elapsed, 1)},
                }

            height, width = frame.shape[:2]
            fps = capture.get(cv2.CAP_PROP_FPS) or 0
            message = (f"The camera at {self._safe(url)} is alive: one {width}x{height} frame "
                       f"in {elapsed:.1f}s" + (f", reporting {fps:.0f} fps" if fps else "") + ".")

            if self._wants_detection(params.get("detect")):
                detected, note = self._detect(frame)
                message += f" {note}"
                return {
                    "status": "success", "message": message,
                    "data": {"alive": True, "width": width, "height": height,
                             "seconds": round(elapsed, 1), "detections": detected},
                }

            return {
                "status": "success", "message": message,
                "data": {"alive": True, "width": width, "height": height,
                         "seconds": round(elapsed, 1)},
            }
        except Exception as error:                                    # noqa: BLE001
            return {
                "status": "error",
                "message": f"Checking {self._safe(url)} failed outright: {error}",
                "data": {"alive": False, "stage": "error"},
            }
        finally:
            if capture is not None:
                capture.release()

    def _detect(self, frame):
        """Objects in the frame, reusing the exported model. Never raises."""
        try:
            from core.config import SETTINGS, vision_model_path

            model_dir = vision_model_path(SETTINGS)
            if not model_dir.exists():
                return {}, "I could not say what is in the frame: the detection model is not exported."
            from ultralytics import YOLO

            model = YOLO(str(model_dir), task="detect")
            results = model.predict(source=frame, conf=SETTINGS["vision"]["confidence"], verbose=False)
            counts = {}
            for result in results:
                for box in result.boxes:
                    label = model.names[int(box.cls[0])]
                    counts[label] = counts.get(label, 0) + 1
        except Exception as error:                                    # noqa: BLE001
            return {}, f"I could not run detection on the frame: {error}"

        if not counts:
            return {}, "Nothing recognisable is in the frame."
        described = ", ".join(f"{count} {label}{'s' if count > 1 else ''}"
                             for label, count in sorted(counts.items()))
        return counts, f"In the frame: {described}."

    @staticmethod
    def _tcp_probe(url: str):
        """(reachable, detail, seconds). A connect, not a request — cheap and decisive."""
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False, "no host in the URL", 0.0
        port = parsed.port or DEFAULT_PORTS.get(parsed.scheme, 554)

        started = time.time()
        try:
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS):
                return True, f"{host}:{port} accepted a connection", time.time() - started
        except socket.gaierror:
            return False, f"'{host}' does not resolve", time.time() - started
        except socket.timeout:
            return False, f"{host}:{port} did not answer within {CONNECT_TIMEOUT_SECONDS}s", time.time() - started
        except OSError as error:
            return False, f"{host}:{port} refused the connection ({error.strerror or error})", time.time() - started

    @staticmethod
    def _wants_detection(value):
        return str(value).strip().lower() in {"yes", "true", "1", "on", "detect"}

    @staticmethod
    def _safe(url: str) -> str:
        """Strip credentials before the URL goes anywhere a transcript might keep it."""
        if "@" not in url or "//" not in url:
            return url
        scheme, _, rest = url.partition("//")
        _credentials, _, host = rest.rpartition("@")
        return f"{scheme}//{host}"


def setup():
    return CheckCameraStreamSkill()
