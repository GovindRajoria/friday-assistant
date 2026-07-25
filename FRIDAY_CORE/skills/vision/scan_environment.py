# skills/vision/scan_environment.py
import platform
from collections import Counter

import cv2
from ultralytics import YOLO

from core.config import SETTINGS, vision_model_path


class ScanEnvironmentSkill:
    def __init__(self, settings=None):
        self.manifest = {
            "name": "scan_environment",
            "description": "Uses the computer's optical sensor (webcam) to take a picture and run object detection. Use this ONLY when the user explicitly asks what you see, what is in front of you, what you are looking at, or what is in the room.",
            "parameters": []
        }

        self.settings = settings or SETTINGS
        self.camera_index = self.settings["vision"]["camera_index"]
        model_dir = vision_model_path(self.settings)

        if not model_dir.exists():
            raise FileNotFoundError(
                f"OpenVINO model not found at {model_dir}. "
                "Generate it with: python benchmarks/openvino_yolo11_opt.py"
            )

        # Load the exported YOLO11 OpenVINO IR model.
        print("[*] Warming up Optimized Optical Engine (YOLO11-IR)...")
        self.model = YOLO(str(model_dir), task='detect')

    def _open_camera(self):
        """Open the webcam, preferring DirectShow on Windows for faster startup."""
        if platform.system() == "Windows":
            return cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        return cv2.VideoCapture(self.camera_index)

    def execute(self, params=None):
        print("[*] FRIDAY is accessing the optical sensor...")

        cap = self._open_camera()

        if not cap.isOpened():
            return {"status": "error", "message": "I could not access the optical sensor. The webcam might be disconnected or locked by another application."}

        ret, frame = cap.read()

        # Release the hardware immediately so nothing else is locked out.
        cap.release()

        if not ret:
            return {"status": "error", "message": "I accessed the camera hardware, but failed to capture a clear visual frame."}

        print("[*] Running tensor inference...")
        results = self.model.predict(
            source=frame,
            conf=self.settings["vision"]["confidence"],
            verbose=False,
        )

        detected_objects = []
        for r in results:
            for box in r.boxes:
                class_id = int(box.cls[0])
                detected_objects.append(self.model.names[class_id])

        if not detected_objects:
            return {"status": "success", "message": "I scanned the environment, but my optical models do not recognize any distinct objects in the current frame."}

        # Group and count the items, e.g. {'person': 1, 'laptop': 2}
        counts = Counter(detected_objects)
        item_strings = [f"{count} {item}{'s' if count > 1 else ''}" for item, count in counts.items()]

        if len(item_strings) > 1:
            items_text = ", ".join(item_strings[:-1]) + f", and {item_strings[-1]}"
        else:
            items_text = item_strings[0]

        observation = f"I am currently looking through the optical sensor. I can see {items_text} in my field of view."
        return {"status": "success", "message": observation}


def setup():
    return ScanEnvironmentSkill()
