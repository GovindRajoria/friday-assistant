# tests/test_scheduler.py
"""Reminders and the briefing, against a fake clock and no server.

Everything the scheduler decides is a function of the time and the config, so
all of it is testable without waiting — `run_cycle(now=...)` takes the clock
as an argument for exactly that reason. Nothing here starts a thread.
"""
from datetime import datetime, time, timedelta, timezone

import pytest
from core import reminders
from core.scheduler import Scheduler, in_quiet_hours, parse_hhmm


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the reminder store at a temp file so tests never touch the real one."""
    monkeypatch.setattr("core.reminders.CONFIG_DIR", tmp_path)
    yield tmp_path


def _settings(**proactive):
    base = {"briefing_enabled": False, "briefing_time": "08:00",
            "quiet_start": "22:00", "quiet_end": "07:00"}
    base.update(proactive)
    return {"proactive": base, "assistant": {"address_user_as": "Sir"}}


class _Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, text):
        self.events.append((kind, text))


# -- quiet hours -----------------------------------------------------------

def test_quiet_hours_wrap_past_midnight():
    # The case a naive `start <= now < end` gets silently wrong: written that
    # way, 22:00–07:00 is never true and the assistant talks all night.
    start, end = time(22, 0), time(7, 0)

    assert in_quiet_hours(time(23, 30), start, end)
    assert in_quiet_hours(time(3, 0), start, end)
    assert not in_quiet_hours(time(12, 0), start, end)
    assert not in_quiet_hours(time(21, 59), start, end)


def test_quiet_hours_within_one_day_still_work():
    assert in_quiet_hours(time(13, 0), time(12, 0), time(14, 0))
    assert not in_quiet_hours(time(15, 0), time(12, 0), time(14, 0))


def test_equal_bounds_mean_never_quiet():
    assert not in_quiet_hours(time(3, 0), time(9, 0), time(9, 0))


def test_a_malformed_time_falls_back_rather_than_raising():
    # Hand-edited config should not take the scheduler thread down.
    assert parse_hhmm("not a time", time(8, 0)) == time(8, 0)
    assert parse_hhmm(None, time(8, 0)) == time(8, 0)
    assert parse_hhmm("06:30", time(8, 0)) == time(6, 30)


# -- reminders -------------------------------------------------------------

def test_a_due_reminder_fires_once_and_is_removed():
    now = datetime.now(timezone.utc)
    reminders.add("stand up", now - timedelta(minutes=1))
    recorder = _Recorder()
    scheduler = Scheduler(_settings(), {}, on_event=recorder)

    scheduler.run_cycle(now=now.astimezone())
    scheduler.run_cycle(now=now.astimezone())

    assert len(recorder.events) == 1
    assert recorder.events[0][0] == "reminder"
    assert "stand up" in recorder.events[0][1]
    assert reminders.load() == []


def test_a_future_reminder_does_not_fire():
    now = datetime.now(timezone.utc)
    reminders.add("later", now + timedelta(hours=2))
    recorder = _Recorder()

    Scheduler(_settings(), {}, on_event=recorder).run_cycle(now=now.astimezone())

    assert recorder.events == []
    assert len(reminders.load()) == 1


def test_an_overdue_reminder_fires_late_and_says_so():
    # A laptop that was shut must still deliver it. Silence is the one
    # outcome the operator cannot recover from, but arriving hours late with
    # no explanation reads as the assistant being confused about the time.
    now = datetime.now(timezone.utc)
    reminders.add("call the site", now - timedelta(hours=3))
    recorder = _Recorder()

    Scheduler(_settings(), {}, on_event=recorder).run_cycle(now=now.astimezone())

    assert "was due at" in recorder.events[0][1]
    assert "call the site" in recorder.events[0][1]


def test_cancelling_stops_it_firing():
    now = datetime.now(timezone.utc)
    record = reminders.add("cancel me", now - timedelta(minutes=1))
    assert reminders.cancel(record["id"])
    recorder = _Recorder()

    Scheduler(_settings(), {}, on_event=recorder).run_cycle(now=now.astimezone())

    assert recorder.events == []


# -- the briefing ----------------------------------------------------------

def test_no_briefing_when_it_is_switched_off():
    at_eight = datetime.now().astimezone().replace(hour=8, minute=1)
    recorder = _Recorder()

    Scheduler(_settings(briefing_enabled=False), {}, on_event=recorder).run_cycle(now=at_eight)

    assert recorder.events == []


def test_the_briefing_fires_once_a_day(monkeypatch):
    monkeypatch.setattr("core.scheduler.compose", lambda skills: "Good morning, Sir.")
    at_eight = datetime.now().astimezone().replace(hour=8, minute=1, second=0, microsecond=0)
    recorder = _Recorder()
    scheduler = Scheduler(_settings(briefing_enabled=True), {}, on_event=recorder)

    scheduler.run_cycle(now=at_eight)
    scheduler.run_cycle(now=at_eight + timedelta(minutes=5))

    assert [kind for kind, _ in recorder.events] == ["briefing"]


def test_a_briefing_missed_by_hours_is_dropped(monkeypatch):
    # Unlike a reminder. A breakfast briefing delivered at lunchtime is stale
    # noise, not news — the machine was asleep and the moment has passed.
    monkeypatch.setattr("core.scheduler.compose", lambda skills: "Good morning, Sir.")
    late = datetime.now().astimezone().replace(hour=15, minute=0, second=0, microsecond=0)
    recorder = _Recorder()

    Scheduler(_settings(briefing_enabled=True), {}, on_event=recorder).run_cycle(now=late)

    assert recorder.events == []


def test_nothing_fires_before_the_briefing_time(monkeypatch):
    monkeypatch.setattr("core.scheduler.compose", lambda skills: "Good morning, Sir.")
    early = datetime.now().astimezone().replace(hour=6, minute=0, second=0, microsecond=0)
    recorder = _Recorder()

    Scheduler(_settings(briefing_enabled=True), {}, on_event=recorder).run_cycle(now=early)

    assert recorder.events == []


def test_an_empty_briefing_is_not_announced(monkeypatch):
    # Every source failing means there is nothing to say. Saying "here is
    # your briefing" and then nothing is worse than staying quiet.
    monkeypatch.setattr("core.scheduler.compose", lambda skills: "")
    at_eight = datetime.now().astimezone().replace(hour=8, minute=1, second=0, microsecond=0)
    recorder = _Recorder()

    Scheduler(_settings(briefing_enabled=True), {}, on_event=recorder).run_cycle(now=at_eight)

    assert recorder.events == []
