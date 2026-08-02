# vision/describers/__init__.py
"""Backend selection for the VLM describer.

vision/watcher.py and server/app.py both ask for "the describer" through
get_describer() rather than importing OllamaVLM or OpenVINOVLM directly, so
flipping vlm.backend in settings is the entire swap.
"""
from core.config import SETTINGS

from vision.describers.ollama_vlm import OllamaVLM
from vision.describers.openvino_vlm import OpenVINOVLM


def get_describer(settings=None):
    settings = settings or SETTINGS
    backend = settings["vlm"]["backend"]
    if backend == "ollama":
        return OllamaVLM()
    if backend == "openvino":
        return OpenVINOVLM()
    raise ValueError(f"unknown vlm backend: {backend!r}")
