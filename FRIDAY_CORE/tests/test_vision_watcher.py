# tests/test_vision_watcher.py
"""ScreenWatcher's busy-skip behaviour.

This is the constraint that actually matters for contention — see
vision/watcher.py's docstring for the measured cost of not having it. No
screen, no model: capture and describe are both fakes, and the busy check is
asserted to short-circuit before either one runs, so this needs neither PIL
nor mss installed.
"""
from vision.watcher import ScreenWatcher


class _FakeDescriber:
    def __init__(self):
        self.calls = 0

    def describe(self, png):
        self.calls += 1
        return "a description"


def _settings():
    return {"screen": {"interval_seconds": 999, "change_threshold": 6, "monitor": 1, "max_width": 1280}}


def test_busy_cycle_is_skipped_before_capture_or_describe():
    describer = _FakeDescriber()
    capture_calls = []

    def fake_capture():
        capture_calls.append(1)
        return b"not-a-real-png"

    watcher = ScreenWatcher(_settings(), describer, capture=fake_capture, is_busy=lambda: True)
    watcher.run_cycle()

    assert capture_calls == []
    assert describer.calls == 0
    assert watcher.latest_description == ""


def test_not_busy_runs_the_cycle_and_stores_the_description(monkeypatch):
    describer = _FakeDescriber()
    watcher = ScreenWatcher(_settings(), describer, capture=lambda: b"frame-bytes", is_busy=lambda: False)

    # The change gate needs a real PNG to hash, which test_vision_capture.py
    # already covers against real Pillow images. Faking the hash here keeps
    # this test about the busy/describe wiring, not image decoding.
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png, hash_size=8: 0)

    watcher.run_cycle()

    assert describer.calls == 1
    assert watcher.latest_description == "a description"


def test_second_identical_frame_does_not_trigger_a_second_describe(monkeypatch):
    describer = _FakeDescriber()
    watcher = ScreenWatcher(_settings(), describer, capture=lambda: b"frame-bytes", is_busy=lambda: False)
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png, hash_size=8: 42)

    watcher.run_cycle()
    watcher.run_cycle()

    assert describer.calls == 1  # the second pass hashed identically and was gated out


def test_start_then_stop_does_not_hang():
    # interval_seconds is large (999s); stop() still returns quickly because
    # threading.Event.wait() unblocks the instant the stop flag is set,
    # regardless of how long the wait timeout is.
    watcher = ScreenWatcher(_settings(), _FakeDescriber(), is_busy=lambda: True)
    watcher.start()
    watcher.stop()
    assert watcher._thread is None


def test_empty_description_is_not_published(monkeypatch):
    # Small vision models return an empty string often enough to matter —
    # observed live against moondream. An empty description must not replace a
    # usable one, or the next prompt carries a blank "what is on screen" line
    # and the HUD shows an empty row.
    class _Describer:
        def __init__(self):
            self.replies = iter(["a terminal window", "   ", ""])

        def describe(self, png):
            return next(self.replies)

    # The change gate hashes a real PNG with PIL; patching it keeps this test
    # free of both PIL and any actual image, and every cycle counts as changed.
    # Values chosen to sit well past change_threshold (6) from each other:
    # consecutive integers differ by a bit or two and would be gated out as
    # "no change", making every cycle after the first a no-op and the test
    # green for the wrong reason.
    hashes = iter([0b0, 0b1111111, 0b1111111_0000000])
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png: next(hashes))

    published = []
    watcher = ScreenWatcher(_settings(), _Describer(), capture=lambda: b"png")
    watcher._on_description = published.append

    for _ in range(3):
        watcher.run_cycle()

    assert published == ["a terminal window"]
    assert watcher.latest_description == "a terminal window"
