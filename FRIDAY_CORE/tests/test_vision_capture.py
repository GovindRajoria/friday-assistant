# tests/test_vision_capture.py
"""Perceptual hash and hamming distance, against synthetic Pillow images.

No screen capture is involved — dhash() takes a PIL Image built entirely in
memory. mss and PIL are not installed in CI
(tests/test_imports_without_runtime_deps.py masks both), so this whole
module is skipped there via importorskip rather than failing to collect.
"""
import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402
from vision.capture import dhash, hamming_distance  # noqa: E402


def _solid(color):
    return Image.new("RGB", (32, 32), color=color)


def _checkerboard():
    image = Image.new("RGB", (32, 32), color=(255, 255, 255))
    pixels = image.load()
    for x in range(32):
        for y in range(32):
            if (x + y) % 2 == 0:
                pixels[x, y] = (0, 0, 0)
    return image


def test_identical_images_have_zero_distance():
    a = dhash(_solid((120, 120, 120)))
    b = dhash(_solid((120, 120, 120)))
    assert hamming_distance(a, b) == 0


def test_near_identical_images_stay_below_the_default_threshold():
    a = dhash(_solid((120, 120, 120)))
    b = dhash(_solid((121, 121, 121)))
    assert hamming_distance(a, b) < 6


def test_clearly_different_images_exceed_the_default_threshold():
    a = dhash(_solid((10, 10, 10)))
    b = dhash(_checkerboard())
    assert hamming_distance(a, b) >= 6
