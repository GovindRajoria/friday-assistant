# tests/test_reminder_parsing.py
"""Turning "in 20 minutes" into a wall-clock time.

Parsed in Python rather than left to the model on purpose: asking a local 8B
model to do arithmetic and formatting in one step is wrong often enough to
matter for something whose entire value is arriving at the right moment.
"""
from datetime import datetime, timedelta

from skills.utility.reminder_control import parse_when

NOW = datetime(2026, 8, 3, 14, 30).astimezone()


def test_relative_minutes():
    assert parse_when("in 20 minutes", NOW) == NOW + timedelta(minutes=20)
    assert parse_when("in 5 mins", NOW) == NOW + timedelta(minutes=5)


def test_relative_hours_and_days():
    assert parse_when("in 2 hours", NOW) == NOW + timedelta(hours=2)
    assert parse_when("in 1 day", NOW) == NOW + timedelta(days=1)


def test_relative_beats_absolute_on_an_ambiguous_phrase():
    # "in 5 hours" also matches the absolute pattern on the bare 5, and would
    # otherwise be read as "at 05:00" — tomorrow morning instead of tonight.
    assert parse_when("in 5 hours", NOW) == NOW + timedelta(hours=5)


def test_absolute_twenty_four_hour():
    assert parse_when("at 16:45", NOW) == NOW.replace(hour=16, minute=45, second=0, microsecond=0)


def test_absolute_with_meridiem():
    assert parse_when("at 4:30pm", NOW) == NOW.replace(hour=16, minute=30, second=0, microsecond=0)
    assert parse_when("at 9pm", NOW) == NOW.replace(hour=21, minute=0, second=0, microsecond=0)


def test_midnight_and_noon_meridiem_edges():
    # 12am is 00:00 and 12pm is 12:00 — the two the naive "+12 if pm" rule
    # gets backwards.
    midnight = parse_when("at 12am", NOW)
    assert midnight.hour == 0
    assert parse_when("at 12pm", NOW).hour == 12


def test_a_time_already_past_today_means_tomorrow():
    # "Remind me at 9am", said at half past two, is never a request to fire
    # immediately.
    result = parse_when("at 9am", NOW)
    assert result.day == NOW.day + 1
    assert result.hour == 9


def test_unparseable_phrases_return_none():
    # Better to say "I could not work out when" than to invent a time and
    # silently fire at the wrong moment.
    assert parse_when("", NOW) is None
    assert parse_when("sometime soon", NOW) is None
    assert parse_when("at 99:99", NOW) is None
