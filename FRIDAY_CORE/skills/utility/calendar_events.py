# skills/utility/calendar_events.py
"""Today's and this week's events, read from local .ics files.

Local files only, and that is a scoping decision rather than a limitation to
apologise for. Google Calendar needs OAuth, a client secret, a token store and a
refresh flow, and this project has no secret store — building half of one to read a
calendar would be the worst version of both. An .ics export or a subscribed calendar
file on disk gets most of the value with none of that.

Recurring events are expanded, because a calendar without them is a calendar that
misses the standup. The expansion is deliberately simple: RRULE FREQ of DAILY,
WEEKLY, MONTHLY or YEARLY with an optional INTERVAL and UNTIL/COUNT, over the query
window only. That covers ordinary recurring meetings and does not pretend to
implement RFC 5545 — anything it cannot expand is reported as such rather than
silently dropped, so a missing event is visible instead of mysterious.

Read-only. There is no path here that writes to a calendar.
"""
from datetime import date, datetime, time, timedelta

from core.config import SETTINGS

MAX_EVENTS = 40
SUPPORTED_FREQ = {"DAILY": 1, "WEEKLY": 7, "MONTHLY": 30, "YEARLY": 365}


class CalendarSkill:
    def __init__(self):
        self.manifest = {
            "name": "calendar",
            "description": (
                "Reads calendar events from local .ics calendar files and reports what is on "
                "today, tomorrow, or over the next week, with times. Parameters: optionally "
                "'when' ('today', 'tomorrow', 'week', or a date). Read-only — it cannot "
                "create or change events. Use reminders to be interrupted at a time, and "
                "task_list for things with no time attached."
            ),
            "parameters": ["when"],
        }

    def execute(self, params=None):
        params = params or {}
        paths = self._calendar_files()
        if not paths:
            return {
                "status": "error",
                "message": ("No calendar files are configured. Export or subscribe to a "
                            "calendar as an .ics file and list it under calendar.ics_paths "
                            "in config/settings.yaml."),
            }

        start, end, label = self._window(params.get("when"))
        try:
            events, unreadable, unexpanded = self._collect(paths, start, end)
        except ImportError as error:
            return {"status": "error", "message": f"Reading calendars needs the icalendar package: {error}"}

        notes = []
        if unreadable:
            notes.append(f"{len(unreadable)} calendar file(s) could not be read: "
                         + ", ".join(unreadable))
        if unexpanded:
            notes.append(f"{unexpanded} recurring event(s) use a repeat rule I do not expand, "
                         "so they may be missing")

        if not events:
            message = f"Nothing is scheduled for {label}."
            return {"status": "success",
                    "message": message + ("\n" + "\n".join(notes) if notes else ""),
                    "data": {"events": 0}}

        lines = []
        for when, summary, location in sorted(events)[:MAX_EVENTS]:
            place = f" — {location}" if location else ""
            lines.append(f"  {self._render_when(when)}  {summary}{place}")
        if len(events) > MAX_EVENTS:
            lines.append(f"  ... and {len(events) - MAX_EVENTS} more")

        message = f"{len(events)} event(s) {label}:\n" + "\n".join(lines)
        return {
            "status": "success",
            "message": message + ("\n" + "\n".join(notes) if notes else ""),
            "data": {"events": len(events), "window": label},
        }

    # ---- gathering --------------------------------------------------------

    @staticmethod
    def _calendar_files():
        from pathlib import Path

        configured = SETTINGS.get("calendar", {}).get("ics_paths") or []
        files = []
        for entry in configured:
            path = Path(str(entry)).expanduser()
            if path.is_dir():
                files.extend(sorted(path.glob("*.ics")))
            elif path.is_file():
                files.append(path)
        return files

    def _collect(self, paths, start, end):
        import icalendar

        events, unreadable, unexpanded = [], [], 0
        for path in paths:
            try:
                calendar = icalendar.Calendar.from_ical(path.read_bytes())
            except Exception:                                         # noqa: BLE001
                unreadable.append(path.name)
                continue

            for component in calendar.walk("VEVENT"):
                summary = str(component.get("SUMMARY") or "(no title)")
                location = str(component.get("LOCATION") or "").strip()
                begin = component.get("DTSTART")
                if begin is None:
                    continue
                first = self._as_datetime(begin.dt)
                if first is None:
                    continue

                rule = component.get("RRULE")
                if rule is None:
                    if start <= first <= end:
                        events.append((first, summary, location))
                    continue

                occurrences, understood = self._expand(first, rule, start, end)
                if not understood:
                    unexpanded += 1
                    # Still include the original if it happens to fall in range.
                    if start <= first <= end:
                        events.append((first, summary, location))
                    continue
                events.extend((moment, summary, location) for moment in occurrences)

        return events, unreadable, unexpanded

    def _expand(self, first, rule, start, end):
        """(occurrences in window, whether the rule was understood)."""
        frequency = str((rule.get("FREQ") or [""])[0]).upper()
        if frequency not in SUPPORTED_FREQ:
            return [], False

        try:
            interval = int((rule.get("INTERVAL") or [1])[0])
        except (TypeError, ValueError):
            interval = 1
        if interval < 1:
            return [], False

        until = None
        if rule.get("UNTIL"):
            until = self._as_datetime(rule.get("UNTIL")[0])
        count_limit = None
        if rule.get("COUNT"):
            try:
                count_limit = int((rule.get("COUNT"))[0])
            except (TypeError, ValueError):
                count_limit = None

        step = timedelta(days=SUPPORTED_FREQ[frequency] * interval)
        occurrences = []
        moment, emitted = first, 0
        # Bounded by the window, and by a hard iteration cap so a malformed rule
        # with a distant start cannot spin.
        for _ in range(2000):
            if moment > end:
                break
            if until is not None and moment > until:
                break
            if count_limit is not None and emitted >= count_limit:
                break
            if moment >= start:
                occurrences.append(moment)
            moment += step
            emitted += 1
        return occurrences, True

    # ---- window and formatting -------------------------------------------

    @staticmethod
    def _as_datetime(value):
        """.ics gives date for all-day events and datetime otherwise; normalise to aware."""
        if isinstance(value, datetime):
            return value.astimezone() if value.tzinfo else value.astimezone()
        if isinstance(value, date):
            return datetime.combine(value, time.min).astimezone()
        return None

    @staticmethod
    def _window(when):
        now = datetime.now().astimezone()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        text = str(when or "today").strip().lower()

        if text in {"today", ""}:
            return today, today + timedelta(days=1) - timedelta(seconds=1), "today"
        if text == "tomorrow":
            begin = today + timedelta(days=1)
            return begin, begin + timedelta(days=1) - timedelta(seconds=1), "tomorrow"
        if text in {"week", "this week", "next week", "7 days", "coming week"}:
            days = int(SETTINGS.get("calendar", {}).get("days_ahead", 7))
            return today, today + timedelta(days=days), f"in the next {days} days"
        if text in {"month", "this month", "30 days"}:
            return today, today + timedelta(days=30), "in the next 30 days"

        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(text, fmt).astimezone()
                begin = parsed.replace(hour=0, minute=0, second=0, microsecond=0)
                return begin, begin + timedelta(days=1) - timedelta(seconds=1), parsed.strftime("%d %b %Y")
            except ValueError:
                continue
        return today, today + timedelta(days=1) - timedelta(seconds=1), f"today (I did not understand '{when}')"

    @staticmethod
    def _render_when(moment: datetime) -> str:
        today = datetime.now().astimezone().date()
        if moment.date() == today:
            return f"today {moment.strftime('%H:%M')}"
        if moment.date() == today + timedelta(days=1):
            return f"tomorrow {moment.strftime('%H:%M')}"
        return moment.strftime("%a %d %b %H:%M")


def setup():
    return CalendarSkill()
