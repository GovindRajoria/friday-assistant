# skills/vision/ocr_screen.py
"""Read the characters on screen, exactly, using the OCR engine already in Windows.

**Deliberately distinct from `describe_screen`.** That one asks a small vision
model what the screen looks like and is frequently wrong in detail — moondream
once returned `urn:ietf:wg:ac:200` as a description of a desktop. This one does
not ask a model anything. It extracts glyphs, so an error dialog, a stack trace,
a serial number or a code snippet in a window with no copyable text comes back as
text that can be quoted rather than paraphrased.

Windows ships `Windows.Media.Ocr`, so there is no second engine to install and no
tesseract binary to find (there is none on this machine, which is why this and
not pytesseract). The trade-off is that `winsdk` is Windows-only and its API is
asynchronous, hence the small event loop below.

The bitmap is built from the captured bytes in memory rather than via a temporary
file. Screen contents are exactly the thing not to leave lying around on disk.
"""
import asyncio

# Both the pixel format and the region parsing are shared with screenshot.py's
# conventions; the two skills are siblings on purpose.
DEFAULT_MONITOR = 1
MAX_CHARS = 8000


class OcrScreenSkill:
    def __init__(self):
        self.manifest = {
            "name": "ocr_screen",
            "description": (
                "Reads the actual text off the screen character by character — an error "
                "message, a stack trace, a code snippet, a serial number, any window whose "
                "text cannot be copied. Parameters: optionally 'region' as "
                "'left,top,width,height' and 'monitor'. This is exact extraction, not a "
                "description: use describe_screen to be told what the screen looks like, and "
                "this when the precise words matter."
            ),
            "parameters": ["region", "monitor"],
        }

    def execute(self, params=None):
        params = params or {}
        try:
            import mss
        except ImportError as error:
            return {"status": "error", "message": f"Screen capture needs the mss package: {error}"}

        region = self._region(params.get("region"))
        try:
            # mss.MSS(), not the deprecated mss.mss(); see vision/capture.py.
            with mss.MSS() as camera:
                if region:
                    grab = camera.grab(region)
                else:
                    index = self._monitor_index(params.get("monitor"), len(camera.monitors))
                    grab = camera.grab(camera.monitors[index])
                pixels, width, height = bytes(grab.raw), grab.size.width, grab.size.height
        except Exception as error:                                    # noqa: BLE001
            return {"status": "error", "message": f"Could not capture the screen: {error}"}

        try:
            lines = asyncio.run(self._recognise(pixels, width, height))
        except ImportError as error:
            return {
                "status": "error",
                "message": ("Text recognition needs the Windows OCR engine via the winsdk "
                            f"package, which is not available here: {error}"),
            }
        except Exception as error:                                    # noqa: BLE001
            return {"status": "error", "message": f"Text recognition failed: {error}"}

        if not lines:
            scope = "that region" if region else "the screen"
            return {
                "status": "success",
                "message": f"I found no readable text on {scope}.",
                "data": {"lines": 0},
            }

        text = "\n".join(lines)
        truncated = len(text) > MAX_CHARS
        body = text[:MAX_CHARS] + ("\n[truncated]" if truncated else "")
        scope = f"region {width}x{height}" if region else f"{width}x{height} screen"
        return {
            "status": "success",
            "message": f"Text read from the {scope} ({len(lines)} line(s)):\n{body}",
            "data": {"lines": len(lines), "characters": len(text), "truncated": truncated},
        }

    async def _recognise(self, pixels: bytes, width: int, height: int) -> list[str]:
        """BGRA bytes to lines of text, through Windows.Media.Ocr."""
        from winsdk.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.security.cryptography import CryptographicBuffer

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError(
                "Windows has no OCR language pack installed for the current user profile. "
                "Settings > Time & language > Language & region > (your language) > "
                "Language options > Optical character recognition."
            )

        # mss hands back BGRA in its `raw` buffer, which is exactly the format
        # SoftwareBitmap wants — no conversion, no intermediate image library.
        buffer = CryptographicBuffer.create_from_byte_array(pixels)
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            buffer, BitmapPixelFormat.BGRA8, width, height
        )

        result = await engine.recognize_async(bitmap)
        return [line.text for line in result.lines if line.text.strip()]

    @staticmethod
    def _monitor_index(value, available):
        try:
            index = int(value)
        except (TypeError, ValueError):
            return DEFAULT_MONITOR
        return index if 0 <= index < available else DEFAULT_MONITOR

    @staticmethod
    def _region(value):
        if not value:
            return None
        parts = [p.strip() for p in str(value).replace(";", ",").split(",")]
        if len(parts) != 4:
            return None
        try:
            left, top, width, height = (int(float(p)) for p in parts)
        except ValueError:
            return None
        if width <= 0 or height <= 0:
            return None
        return {"left": left, "top": top, "width": width, "height": height}


def setup():
    return OcrScreenSkill()
