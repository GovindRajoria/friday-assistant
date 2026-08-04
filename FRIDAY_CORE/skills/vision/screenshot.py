# skills/vision/screenshot.py
"""Save a PNG of the screen, or a region of it, into the workspace.

Distinct from `describe_screen`, which asks a small vision model what the screen
looks like and is unreliable in detail. This produces a file — evidence rather
than an impression — and the file is the thing a human can then look at.

It writes, so the destination goes through the same allowlist as everything else
and defaults to the workspace root. `mss` is imported inside execute() for the
usual reason: CI does not install it, and a module-scope import would delete this
skill from the registry rather than report a missing package.
"""
import time

from core.paths import allowed_roots, refusal, resolve_within


class ScreenshotSkill:
    def __init__(self):
        self.manifest = {
            "name": "screenshot",
            "description": (
                "Captures the screen to a PNG file in the workspace and reports where it "
                "saved it. Parameters: optionally 'path' for the filename, 'monitor' for "
                "which display, and 'region' as 'left,top,width,height' for part of the "
                "screen. Use describe_screen instead to be told what is on screen in words; "
                "use this when a picture needs to exist as a file."
            ),
            "parameters": ["path", "monitor", "region"],
        }

    def execute(self, params=None):
        params = params or {}
        roots = allowed_roots()
        if not roots:
            return {"status": "error", "message": "No workspace root is configured to save into."}

        name = params.get("path") or f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        if not str(name).lower().endswith(".png"):
            name = f"{name}.png"
        destination = resolve_within(str(name), roots)
        if destination is None:
            return refusal(str(name))

        try:
            import mss
            import mss.tools
        except ImportError as error:
            return {"status": "error", "message": f"Screen capture needs the mss package: {error}"}

        region = self._region(params.get("region"))
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # mss.MSS(), not the deprecated mss.mss() — vision/capture.py made
            # the same call for the same reason.
            with mss.MSS() as camera:
                if region:
                    grab = camera.grab(region)
                else:
                    index = self._monitor_index(params.get("monitor"), len(camera.monitors))
                    grab = camera.grab(camera.monitors[index])
                mss.tools.to_png(grab.rgb, grab.size, output=str(destination))
        except Exception as error:                                    # noqa: BLE001
            return {"status": "error", "message": f"Could not capture the screen: {error}"}

        size_kb = destination.stat().st_size / 1024
        scope = "region" if region else f"monitor {self._monitor_index(params.get('monitor'), 99)}"
        return {
            "status": "success",
            "message": (f"Saved a {grab.size.width}x{grab.size.height} screenshot of {scope} "
                        f"to {destination} ({size_kb:.0f} KB)."),
            "data": {"path": str(destination), "width": grab.size.width, "height": grab.size.height},
        }

    @staticmethod
    def _monitor_index(value, available):
        """mss.monitors[0] is the union of all displays; 1 is the primary."""
        try:
            index = int(value)
        except (TypeError, ValueError):
            return 1
        return index if 0 <= index < available else 1

    @staticmethod
    def _region(value):
        """'left,top,width,height' to the dict mss wants, or None."""
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
    return ScreenshotSkill()
