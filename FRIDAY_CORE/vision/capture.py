# vision/capture.py
"""Screen capture and the perceptual-hash change gate.

mss and PIL are imported inside functions, not at module scope. Neither is
installed in CI (tests/test_imports_without_runtime_deps.py masks both), and
this module sits in server/app.py's import chain once the screen watcher is
wired in — a module-scope import here would make the whole server
uncollectable there, the same failure core.speaker's pyttsx3 import once
caused on the first push (see that test's docstring).

dhash() and hamming_distance() take a PIL Image and two ints respectively —
no file I/O, nothing about a live screen — so they are unit-testable against
synthetic Pillow images built entirely in memory, with no monitor attached.
"""
import io


def dhash(image, hash_size: int = 8) -> int:
    """Difference hash: shrink to grayscale, compare each pixel to its right neighbour.

    Implemented directly rather than adding an imagehash dependency for what
    is, in full, a resize, a grayscale convert, and a row of comparisons.
    """
    small = image.convert("L").resize((hash_size + 1, hash_size))
    # tobytes() rather than getdata(): on an "L" image the two give the same
    # sequence of byte values, and getdata() is deprecated for removal in
    # Pillow 14.
    pixels = small.tobytes()

    bits = 0
    for row in range(hash_size):
        offset = row * (hash_size + 1)
        for col in range(hash_size):
            bits = (bits << 1) | (1 if pixels[offset + col] > pixels[offset + col + 1] else 0)
    return bits


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def grab_png(monitor: int, max_width: int) -> bytes:
    """Capture `monitor`, downscale to `max_width` wide, return PNG-encoded bytes.

    mss.mss() is deprecated in the version pinned here (10.2.0); mss.MSS()
    is the replacement and what this uses.
    """
    import mss
    from PIL import Image

    with mss.MSS() as sct:
        # mss numbers real monitors from 1 — 0 is "all monitors combined",
        # never what a single describe pass wants.
        frame = sct.grab(sct.monitors[monitor])

    image = Image.frombytes("RGB", frame.size, frame.bgra, "raw", "BGRX")
    if image.width > max_width:
        scale = max_width / image.width
        image = image.resize((max_width, max(1, round(image.height * scale))))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def dhash_from_png(png: bytes, hash_size: int = 8) -> int:
    """dhash of PNG-encoded bytes — the shape grab_png returns."""
    from PIL import Image

    return dhash(Image.open(io.BytesIO(png)), hash_size)
