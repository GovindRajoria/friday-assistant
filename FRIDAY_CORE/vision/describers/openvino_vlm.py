# vision/describers/openvino_vlm.py
"""OpenVINO VLM backend — not implemented.

Documented as a placeholder rather than left out entirely: the point of the
Describer protocol is that this class implementing describe() one day is the
entire swap. vision/watcher.py and server/app.py need no changes for it;
only vlm.backend in settings and this method's body.
"""


class OpenVINOVLM:
    def describe(self, png: bytes) -> str:
        raise NotImplementedError("OpenVINO VLM backend is not implemented yet")
