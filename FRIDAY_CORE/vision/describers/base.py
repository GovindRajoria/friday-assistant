# vision/describers/base.py
"""The Describer contract every VLM backend implements.

One method, one job: turn PNG bytes into a short natural-language
description of what is on screen. Keeping the contract this narrow is what
lets a second backend (OpenVINOVLM) sit behind the same call site with zero
changes to vision/watcher.py — the swap the operator asked to keep open.
"""
from typing import Protocol


class Describer(Protocol):
    def describe(self, png: bytes) -> str:
        ...
