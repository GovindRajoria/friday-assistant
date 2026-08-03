# core/scheduler.py
"""The one thing in this assistant that speaks without being spoken to.

A background thread on a slow tick. It owns two kinds of work: reminders the
operator set, and a daily briefing at a configured time. Both surface through
a callback the caller supplies, exactly like vision/watcher.py — this module
knows nothing about sockets, speech or event envelopes, which is what keeps
it testable with a fake clock and no server running.

Three decisions worth stating, because unattended code that gets them wrong
is the kind that gets switched off permanently:

  * Off by default. Something that talks at you unprompted is opt-in.
  * Quiet hours gate the *speech*, not the event. A briefing at 03:00 should
    not wake the house; it should still be on screen in the morning.
  * A missed briefing is dropped, a missed reminder fires late. Those differ
    on purpose. An 08:00 briefing delivered at 15:00 is stale noise, while a
    reminder is a promise the operator stopped holding themselves — late is
    recoverable, silence is not.
"""
import threading
from datetime import date, datetime, time, timezone

from core import reminders
from core.briefing import compose

# Slow on purpose. Nothing here is time-critical to the second, and a thread
# waking twice a minute is invisible; one waking constantly is a battery
# complaint.
DEFAULT_TICK_SECONDS = 30
# How late a briefing may be and still be worth delivering. Past this the
# backend was presumably down or asleep, and a breakfast briefing at lunchtime
# is noise rather than news.
BRIEFING_GRACE_MINUTES = 45


def parse_hhmm(value: str, fallback: time) -> time:
    """"08:00" to a time, tolerating whatever ends up in a hand-edited config."""
    try:
        hour, _, minute = str(value).partition(":")
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return fallback


def in_quiet_hours(now: time, start: time, end: time) -> bool:
    """Quiet hours usually wrap past midnight, so this is not a simple range.

    22:00–07:00 means "after 22:00 OR before 07:00". Written as one comparison
    it silently means "never", which is the bug this function exists to avoid.
    """
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


class Scheduler:
    """Fires due work through `on_event(kind, text)` on its own thread.

    `kind` is "reminder" or "briefing"; the caller decides what to do with
    each. `active_skills` is passed straight to the briefing composer.
    """

    def __init__(self, settings, active_skills, on_event, on_error=None, tick_seconds=DEFAULT_TICK_SECONDS):
        self._settings = settings
        self._active_skills = active_skills
        self._on_event = on_event
        self._on_error = on_error or (lambda error: None)
        self._tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread = None
        # Which date the briefing has already gone out for. Guards against a
        # second delivery when the tick lands twice inside the same minute.
        self._briefed_on: date | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="friday-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- the loop ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_cycle()
            except Exception as error:  # noqa: BLE001 — a scheduler that dies stops every future job silently
                # Reported and kept alive, the same lesson vision/watcher.py
                # learned: the first failure killed the thread outright and
                # screen awareness simply stopped with nothing said about it.
                self._on_error(error)
            self._stop.wait(self._tick_seconds)

    def run_cycle(self, now: datetime | None = None) -> None:
        """One tick. Takes `now` so the whole schedule is testable with a fake clock."""
        now = now or datetime.now().astimezone()

        for item in reminders.due_now(now.astimezone(timezone.utc)):
            self._on_event("reminder", self._reminder_text(item, now))

        if self._briefing_due(now):
            self._briefed_on = now.date()
            text = compose(self._active_skills)
            if text:
                self._on_event("briefing", text)

    # -- the two job kinds -------------------------------------------------

    def _reminder_text(self, item: dict, now: datetime) -> str:
        text = item.get("text", "(no text)")
        try:
            due = datetime.fromisoformat(item["due"])
        except (KeyError, TypeError, ValueError):
            return f"Reminder: {text}"
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        late = (now - due).total_seconds()
        if late > 300:
            # Said out loud, because a reminder arriving hours late without
            # saying so reads as the assistant being confused about the time.
            local = due.astimezone().strftime("%H:%M")
            return f"Reminder, which was due at {local}: {text}"
        return f"Reminder: {text}"

    def _briefing_due(self, now: datetime) -> bool:
        config = self._settings.get("proactive", {})
        if not config.get("briefing_enabled", False):
            return False
        if self._briefed_on == now.date():
            return False

        scheduled = parse_hhmm(config.get("briefing_time", "08:00"), time(8, 0))
        target = now.replace(hour=scheduled.hour, minute=scheduled.minute, second=0, microsecond=0)
        if now < target:
            return False
        # Dropped rather than delivered once the grace window has passed —
        # see the module docstring for why this differs from reminders.
        return (now - target).total_seconds() <= BRIEFING_GRACE_MINUTES * 60
