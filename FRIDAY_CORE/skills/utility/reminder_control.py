# skills/utility/reminder_control.py
"""Set, list and cancel reminders in plain language.

The skill only writes records; core/scheduler.py is what delivers them. That
split matters — delivery has to keep working when no turn is running and no
client is connected, which a skill cannot do.

Relative times ("in 20 minutes") are parsed here rather than left to the
model. Asking a local 8B model to compute a wall-clock time from "in an hour
and a half" is asking it to do arithmetic and formatting in one step, and it
is wrong often enough to matter for something whose whole value is arriving
at the right moment.
"""
import re
from datetime import datetime, timedelta

from core import reminders

# "in 20 minutes", "in 2 hours", "in 90 mins", "in 1 day"
RELATIVE = re.compile(r"(?:in\s+)?(\d+)\s*(min|mins|minute|minutes|hour|hours|hr|hrs|day|days)\b", re.IGNORECASE)
# "at 16:30", "at 4:30pm", "at 9pm"
ABSOLUTE = re.compile(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)

UNIT_SECONDS = {
    "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
}


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Turn "in 20 minutes" or "at 4:30pm" into a local datetime, or None.

    Relative is tried first: "in 5 hours" also matches the absolute pattern
    on the bare number, and would otherwise be read as "at 05:00".
    """
    now = now or datetime.now().astimezone()
    text = (text or "").strip()
    if not text:
        return None

    relative = RELATIVE.search(text)
    if relative:
        amount = int(relative.group(1))
        return now + timedelta(seconds=amount * UNIT_SECONDS[relative.group(2).lower()])

    absolute = ABSOLUTE.search(text)
    if absolute:
        hour = int(absolute.group(1))
        minute = int(absolute.group(2) or 0)
        meridiem = (absolute.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # A time already past today means tomorrow. "Remind me at 9am" said at
        # 10am is never a request to fire immediately.
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


class ReminderControlSkill:
    def __init__(self):
        self.manifest = {
            "name": "reminders",
            "description": (
                "Sets, lists and cancels reminders that FRIDAY will deliver on its own "
                "at the right time, even if nothing is open. Use this whenever the user "
                "asks to be reminded of something. Parameters: 'action' ('set', 'list' "
                "or 'cancel'), 'text' (what to be reminded of), 'when' (a phrase such as "
                "'in 20 minutes' or 'at 4:30pm'), and 'id' (for cancel)."
            ),
            "parameters": ["action", "text", "when", "id"],
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "set").lower()

        if action == "list":
            return self._list()
        if action == "cancel":
            return self._cancel(params.get("id"))
        if action == "set":
            return self._set(params.get("text"), params.get("when"))
        return {"status": "error", "message": f"Unknown reminder action: {action}"}

    def _set(self, text, when) -> dict:
        text = str(text or "").strip()
        if not text:
            return {"status": "error", "message": "I need to know what to remind you about."}

        due = parse_when(str(when or ""))
        if due is None:
            return {"status": "error",
                    "message": "I could not work out when. Try 'in 20 minutes' or 'at 4:30pm'."}

        try:
            record = reminders.add(text, due)
        except (ValueError, OSError) as error:
            return {"status": "error", "message": f"I could not save that reminder: {error}"}

        return {"status": "success",
                "message": f"Reminder set for {due.strftime('%H:%M on %d %b')}: {text} (id {record['id']})",
                "data": {"id": record["id"], "due": record["due"]}}

    def _list(self) -> dict:
        stored = reminders.load()
        if not stored:
            return {"status": "success", "message": "You have no reminders set."}
        lines = []
        for item in sorted(stored, key=lambda entry: entry.get("due", "")):
            try:
                due = datetime.fromisoformat(item["due"]).astimezone().strftime("%H:%M on %d %b")
            except (KeyError, TypeError, ValueError):
                due = "an unreadable time"
            lines.append(f"{item.get('id', '?')}: {item.get('text', '(no text)')} — {due}")
        return {"status": "success", "message": f"{len(lines)} reminder(s):\n" + "\n".join(lines)}

    def _cancel(self, reminder_id) -> dict:
        reminder_id = str(reminder_id or "").strip()
        if not reminder_id:
            return {"status": "error", "message": "I need the reminder's id. Ask me to list them first."}
        if reminders.cancel(reminder_id):
            return {"status": "success", "message": f"Cancelled reminder {reminder_id}."}
        return {"status": "error", "message": f"There is no reminder with id {reminder_id}."}


def setup():
    return ReminderControlSkill()
