# vision/watcher.py
"""Continuous screen awareness, running entirely off the asyncio event loop.

capture -> change gate -> describe -> store the latest description, on one
background thread. A describe pass blocks on the network exactly like
core.llm_client.chat does everywhere else in this project; running it on the
loop server/app.py serves WebSocket connections from would freeze every one
of them for the duration of each pass.

Contention is measured, not hypothetical. Benchmarking on this machine found
a normal describe pass takes ~0.19s at 1280px, but the same call took 4.86s
when llama3.1 was being exercised at the same time — both models queue for
the same Ollama process. `is_busy` exists so the watcher can be told a turn
is in flight and skip that cycle outright rather than run into exactly that
slowdown on every turn. Skipping, not queueing: a queued describe call would
still land mid-turn and pay the same contention penalty, just a little later.

Failure handling exists because vlm.host can point off-machine (Phase 5) —
a Pi 5 dropping off wifi mid-scan is ordinary, not exotic, and unlike a
console turn this loop has no caller waiting to see an exception. Verified on
this machine before the fix: an injected describer that raised ConnectionError
killed the thread after exactly one attempt, silently, with no message
anywhere — screen awareness stopped forever. run_cycle now catches, reports
through `on_error`, and keeps going; _loop backs off while failing so a dead
host is not re-probed every interval_seconds.
"""
import threading

from vision.capture import dhash_from_png, hamming_distance

# Backoff cap while a describe pass keeps failing, expressed as a multiple of
# interval_seconds. Doubling per consecutive failure and capping the growth
# means a host that stays down is retried at a bounded worst-case cadence
# rather than hammered every interval_seconds or abandoned outright.
MAX_BACKOFF_MULTIPLE = 8


class ScreenWatcher:
    """Capture/describe loop plus the last description it produced.

    `capture` and `describer` are both injected so the busy-skip and the
    change gate can be exercised with fakes in tests — no real screen, no
    real model, and (for the busy path) no dependency on PIL or mss either.
    """

    def __init__(self, settings, describer, capture=None, is_busy=None, on_error=None):
        self._settings = settings
        self._describer = describer
        self._capture = capture or self._grab_configured_frame
        self._is_busy = is_busy or (lambda: False)
        self._on_error = on_error

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest_description = ""
        self._previous_hash: int | None = None
        self._consecutive_failures = 0
        self._on_description = None

    def _grab_configured_frame(self) -> bytes:
        # Imported here rather than at module scope so that constructing a
        # watcher with an injected `capture` (every test does this) never
        # needs mss or PIL installed at all.
        from vision.capture import grab_png

        screen = self._settings["screen"]
        return grab_png(screen["monitor"], screen["max_width"])

    @property
    def latest_description(self) -> str:
        with self._lock:
            return self._latest_description

    def start(self, on_description=None) -> None:
        """Spawn the background thread. A no-op if already running."""
        if self._thread is not None:
            return
        self._on_description = on_description
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to exit and wait briefly for it to do so."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        interval = self._settings["screen"]["interval_seconds"]
        wait = interval
        # Event.wait returns True the instant stop() sets the flag, so a
        # long wait never delays shutdown — the loop only sleeps for `wait`
        # when nobody has asked it to stop.
        while not self._stop_event.wait(wait):
            self.run_cycle()
            wait = self._next_wait(interval)

    def _next_wait(self, interval: float) -> float:
        """Backoff wait for the next cycle. Plain `interval` once healthy again."""
        if self._consecutive_failures == 0:
            return interval
        return min(interval * (2 ** self._consecutive_failures), interval * MAX_BACKOFF_MULTIPLE)

    def run_cycle(self) -> None:
        """Run one capture/describe pass, or skip it. Exposed directly for tests.

        A failure here — capture or describe raising, most often an
        unreachable vlm.host — is caught and reported through `on_error`
        rather than left to kill the background thread.

        Two things make a retry after a failure real rather than incidental.
        First, `_previous_hash = current_hash` now runs only after a
        successful describe, not before it as the pre-Phase-5 code had it —
        a failed cycle can no longer poison the change gate with a hash that
        was never actually described. Second, on failure `_previous_hash` is
        explicitly reset to None, which forces the NEXT cycle to skip the
        gate outright: without it, a failure following a PRIOR success would
        leave the gate comparing against that old baseline, and a later
        frame similar enough to it would be silently gated out with no
        retry and no error, even though it was never the frame that got
        described. Verified both ways: reverting either change independently
        turns tests/test_vision_watcher.py red.
        """
        if self._is_busy():
            # Measured behaviour, not caution: see the module docstring.
            return

        try:
            png = self._capture()
            current_hash = dhash_from_png(png)
            threshold = self._settings["screen"]["change_threshold"]
            if self._previous_hash is not None and hamming_distance(current_hash, self._previous_hash) < threshold:
                return  # not enough on-screen change to justify a describe pass
            description = (self._describer.describe(png) or "").strip()
        except Exception as error:  # noqa: BLE001 — an unreachable host is ordinary, not exotic; must not kill the thread
            self._consecutive_failures += 1
            self._previous_hash = None  # force a retry next cycle regardless of on-screen change
            if self._on_error is not None:
                self._on_error(error)
            return

        self._consecutive_failures = 0
        self._previous_hash = current_hash
        if not description:
            # A small vision model returns an empty string often enough to
            # matter — observed live against moondream. Keeping the previous
            # description is better than replacing a usable one with nothing,
            # and publishing the blank would put an empty "what is on screen"
            # line into the next prompt and an empty row in the HUD.
            return

        with self._lock:
            self._latest_description = description
        if self._on_description is not None:
            self._on_description(description)
