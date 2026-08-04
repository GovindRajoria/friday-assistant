# skills/vision/annotate_image.py
"""Run detection on an image file and write an annotated copy beside it.

Reuses the OpenVINO model already exported for `scan_environment`, so this costs no
new weights and no new export step — the same YOLO11 IR, pointed at a file instead
of the webcam.

Two differences from `scan_environment` that matter:

The model is loaded on first use, not in `setup()`. `scan_environment` raises from
its constructor when the exported model directory is missing, and
`core/registry.py` turns that into a skill that silently does not exist — the
failure `skill_health` was written to expose. Loading here on demand means a
missing model produces a sentence explaining how to regenerate it.

It writes, so the output path goes through the workspace allowlist. Input may come
from the workspace too; the model is never handed an arbitrary path.
"""
from core.paths import allowed_roots, refusal, resolve_within

READABLE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


class AnnotateImageSkill:
    def __init__(self):
        self.manifest = {
            "name": "annotate_image",
            "description": (
                "Runs object detection on an image file in the workspace and saves a copy "
                "with the detected objects boxed and labelled, reporting what it found. "
                "Parameters: 'path' to the image, and optionally 'output' for the annotated "
                "copy. Use scan_environment to detect objects through the webcam instead of "
                "in a file, and describe_screen to be told what is on screen."
            ),
            "parameters": ["path", "output"],
        }
        self._model = None

    def _load_model(self):
        """Load the exported IR once, on demand. Raises with a fixable message."""
        if self._model is not None:
            return self._model

        from core.config import SETTINGS, vision_model_path

        model_dir = vision_model_path(SETTINGS)
        if not model_dir.exists():
            raise FileNotFoundError(
                f"The exported detection model is not at {model_dir}. "
                "Regenerate it with: python benchmarks/openvino_yolo11_opt.py"
            )
        from ultralytics import YOLO

        self._model = YOLO(str(model_dir), task="detect")
        return self._model

    def execute(self, params=None):
        params = params or {}
        path_str = params.get("path")
        if not path_str:
            return {"status": "error", "message": "I need the path of an image to annotate."}

        roots = allowed_roots()
        source = resolve_within(str(path_str), roots)
        if source is None:
            return refusal(str(path_str))
        if not source.is_file():
            return {"status": "error", "message": f"'{source}' is not a readable file."}
        if source.suffix.lower() not in READABLE_SUFFIXES:
            return {
                "status": "error",
                "message": (f"'{source.suffix}' is not an image I can read. "
                            f"I handle: {', '.join(sorted(READABLE_SUFFIXES))}."),
            }

        output_name = params.get("output") or f"{source.stem}-annotated.png"
        destination = resolve_within(str(output_name), roots)
        if destination is None:
            return refusal(str(output_name))

        try:
            model = self._load_model()
        except FileNotFoundError as error:
            return {"status": "error", "message": str(error)}
        except ImportError as error:
            return {"status": "error", "message": f"Detection needs ultralytics: {error}"}

        try:
            import cv2

            frame = cv2.imread(str(source))
            if frame is None:
                return {"status": "error", "message": f"'{source.name}' could not be decoded as an image."}

            from core.config import SETTINGS

            results = model.predict(source=frame, conf=SETTINGS["vision"]["confidence"], verbose=False)
            counts = {}
            for result in results:
                for box in result.boxes:
                    label = model.names[int(box.cls[0])]
                    counts[label] = counts.get(label, 0) + 1

            annotated = results[0].plot() if results else frame
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(destination), annotated):
                return {"status": "error", "message": f"Could not write the annotated image to {destination}."}
        except Exception as error:                                    # noqa: BLE001
            return {"status": "error", "message": f"Detection failed on '{source.name}': {error}"}

        if not counts:
            return {
                "status": "success",
                "message": (f"No objects were detected in {source.name} above the confidence "
                            f"threshold. The unannotated copy is at {destination}."),
                "data": {"path": str(destination), "detections": {}},
            }

        described = ", ".join(f"{count} {label}{'s' if count > 1 else ''}"
                             for label, count in sorted(counts.items()))
        return {
            "status": "success",
            "message": f"Detected {described} in {source.name}. Annotated copy saved to {destination}.",
            "data": {"path": str(destination), "detections": counts},
        }


def setup():
    return AnnotateImageSkill()
