# tests/test_vision_watcher.py
"""ScreenWatcher's busy-skip behaviour, and its survival of a failing describer.

This is the constraint that actually matters for contention — see
vision/watcher.py's docstring for the measured cost of not having it. No
screen, no model: capture and describe are both fakes, and the busy check is
asserted to short-circuit before either one runs, so this needs neither PIL
nor mss installed.
"""
import time

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


def test_run_cycle_catches_a_describe_failure_and_reports_it(monkeypatch):
    # Verified on this machine before this fix: an injected describer that
    # raises ConnectionError produced exactly one describe attempt and a
    # dead watcher thread, with no message anywhere. run_cycle must not let
    # that propagate.
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png: 0)

    class _RaisingDescriber:
        def describe(self, png):
            raise ConnectionError("host unreachable")

    errors = []
    watcher = ScreenWatcher(_settings(), _RaisingDescriber(), capture=lambda: b"frame", on_error=errors.append)

    watcher.run_cycle()  # must not raise

    assert len(errors) == 1
    assert isinstance(errors[0], ConnectionError)
    assert watcher.latest_description == ""


def test_a_failed_describe_forces_a_retry_next_cycle_even_without_on_screen_change(monkeypatch):
    # Regression for the ordering bug this fix closes. The old code set
    # `_previous_hash = current_hash` BEFORE calling describe(), unconditionally.
    # A failed describe against a screen that then stayed static would poison
    # the change gate forever: the next cycle would see "no change" against
    # that stale hash and skip describe entirely, so a recovered host would
    # never be retried unless the screen also happened to change. Every hash
    # below is identical, so the only way the second call reaches describe()
    # at all is the `_previous_hash = None` reset on failure.
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png: 7)

    class _FailsOnceDescriber:
        def __init__(self):
            self.calls = 0

        def describe(self, png):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("host unreachable")
            return "recovered"

    describer = _FailsOnceDescriber()
    errors = []
    watcher = ScreenWatcher(_settings(), describer, capture=lambda: b"frame-bytes", on_error=errors.append)

    watcher.run_cycle()  # first pass: describe raises
    assert describer.calls == 1
    assert len(errors) == 1
    assert watcher.latest_description == ""

    watcher.run_cycle()  # identical (unchanged) screen — must still retry, not gate out
    assert describer.calls == 2
    assert watcher.latest_description == "recovered"


def test_a_failure_after_a_prior_success_still_forces_the_next_cycle_to_retry(monkeypatch):
    # Isolates the second half of the fix, distinct from the test above.
    # That test starts from _previous_hash == None, where the structural
    # move of the assignment to after a successful describe is already
    # enough on its own. This one starts from a REAL prior baseline: without
    # the explicit reset to None on failure, a failure following a prior
    # success would leave the gate comparing the next frame against that old
    # successful baseline rather than skipping outright — and a later frame
    # that happens to resemble the OLD baseline (not the frame that actually
    # failed) would be silently gated out, never retried, and never reported.
    hashes = iter([0, 127, 1])  # baseline, then a distant "failing" frame, then one close to baseline
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png: next(hashes))

    class _Describer:
        def __init__(self):
            self.calls = 0

        def describe(self, png):
            self.calls += 1
            if self.calls == 1:
                return "baseline description"
            if self.calls == 2:
                raise ConnectionError("host unreachable")
            return "recovered description"

    describer = _Describer()
    errors = []
    watcher = ScreenWatcher(_settings(), describer, capture=lambda: b"frame-bytes", on_error=errors.append)

    watcher.run_cycle()  # establishes the baseline
    assert watcher.latest_description == "baseline description"

    watcher.run_cycle()  # distant frame — passes the gate, then describe fails
    assert describer.calls == 2
    assert len(errors) == 1

    watcher.run_cycle()  # frame close to the OLD baseline, not the frame that failed
    assert describer.calls == 3  # true only because the failure forced an unconditional retry
    assert watcher.latest_description == "recovered description"


def test_backoff_grows_with_consecutive_failures_and_resets_on_recovery():
    watcher = ScreenWatcher(_settings(), _FakeDescriber(), is_busy=lambda: False)
    interval = 5

    assert watcher._next_wait(interval) == interval  # healthy: no backoff

    watcher._consecutive_failures = 1
    assert watcher._next_wait(interval) == interval * 2
    watcher._consecutive_failures = 3
    assert watcher._next_wait(interval) == interval * 8  # capped at MAX_BACKOFF_MULTIPLE
    watcher._consecutive_failures = 10
    assert watcher._next_wait(interval) == interval * 8  # still capped, not unbounded

    watcher._consecutive_failures = 0
    assert watcher._next_wait(interval) == interval  # back to normal cadence once healthy


def test_watcher_thread_survives_a_failing_describer_and_recovers_without_a_restart(monkeypatch):
    # The end-to-end version of the two tests above: a real background
    # thread, started once, that fails twice and then comes back — with
    # nothing stopping and restarting it in between. Every hash is identical
    # so recovery can only be happening because of the failure-path fix, not
    # because the screen changed into view again.
    monkeypatch.setattr("vision.watcher.dhash_from_png", lambda png: 1)

    class _FlakyDescriber:
        def __init__(self):
            self.calls = 0

        def describe(self, png):
            self.calls += 1
            if self.calls <= 2:
                raise ConnectionError("host unreachable")
            return "back online"

    errors = []
    settings = {"screen": {"interval_seconds": 0.02, "change_threshold": 6, "monitor": 1, "max_width": 1280}}
    watcher = ScreenWatcher(settings, _FlakyDescriber(), capture=lambda: b"frame-bytes", on_error=errors.append)

    watcher.start()
    try:
        deadline = time.time() + 3
        while watcher.latest_description != "back online" and time.time() < deadline:
            time.sleep(0.01)

        assert watcher.latest_description == "back online"
        assert len(errors) == 2  # two failed cycles, each reported
        assert watcher._thread is not None and watcher._thread.is_alive()
    finally:
        watcher.stop()
