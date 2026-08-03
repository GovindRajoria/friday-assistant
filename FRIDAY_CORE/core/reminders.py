# core/reminders.py
"""One-shot reminders, persisted so they survive a restart.

A reminder the operator set is a promise, and a promise that a backend
restart quietly forgets is worse than no reminder feature at all — they
stopped keeping the thing in their head because they told the assistant.
So this is a file on disk, not a list in memory.

JSON rather than the SQLite vault next door: the whole store is a handful of
records, it is written from one thread on a timer, and a file the operator
can open and read is worth more here than a query language nobody will use.

Git-ignored, and for the same reason as the profile — a reminder body is
whatever they asked to be reminded of, and this repository is public.

Times are stored as UTC ISO-8601 with an explicit offset. A naive local
timestamp is ambiguous across a DST boundary, which for a reminder means
firing an hour early or late exactly twice a year.
"""
import json
import threading
import uuid
from datetime import datetime, timezone

from core.config import CONFIG_DIR

REMINDERS_FILENAME = "reminders.json"
# Enough that a busy week fits; low enough that a runaway loop cannot fill
# the disk through a skill the model can call.
MAX_REMINDERS = 200

# The store is read by the scheduler thread and written by whichever thread
# is running a turn. Both go through this lock; the file is small enough that
# holding it across the whole read-modify-write costs nothing.
_lock = threading.Lock()


def reminders_path():
    return CONFIG_DIR / REMINDERS_FILENAME


def _read_unlocked() -> list[dict]:
    try:
        raw = reminders_path().read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt file must not take the scheduler down on every tick. An
        # empty list loses reminders, which is bad, but a thread that dies
        # loses all future ones too.
        return []
    return data if isinstance(data, list) else []


def _write_unlocked(reminders: list[dict]) -> None:
    path = reminders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reminders, indent=2), encoding="utf-8")


def load() -> list[dict]:
    with _lock:
        return _read_unlocked()


def add(text: str, due: datetime) -> dict:
    """Store one reminder and return it. `due` may be naive local or aware."""
    if due.tzinfo is None:
        due = due.astimezone()
    record = {
        "id": uuid.uuid4().hex[:8],
        "text": text,
        "due": due.astimezone(timezone.utc).isoformat(),
    }
    with _lock:
        reminders = _read_unlocked()
        if len(reminders) >= MAX_REMINDERS:
            raise ValueError(f"there are already {MAX_REMINDERS} reminders stored")
        reminders.append(record)
        _write_unlocked(reminders)
    return record


def cancel(reminder_id: str) -> bool:
    with _lock:
        reminders = _read_unlocked()
        remaining = [item for item in reminders if item.get("id") != reminder_id]
        if len(remaining) == len(reminders):
            return False
        _write_unlocked(remaining)
    return True


def due_now(now: datetime | None = None) -> list[dict]:
    """Remove and return every reminder that has come due.

    Removed in the same locked pass that reads them, so a slow consumer
    cannot be handed the same reminder twice by the next tick.

    Overdue reminders fire rather than being dropped. A laptop that was shut
    when a reminder came due should still deliver it on wake — the operator
    stopped holding that thought themselves, and silence is the one outcome
    they cannot recover from. The caller says how late it was.
    """
    now = now or datetime.now(timezone.utc)
    fired, keeping = [], []
    with _lock:
        for item in _read_unlocked():
            try:
                due = datetime.fromisoformat(item["due"])
            except (KeyError, TypeError, ValueError):
                # An unparseable record would otherwise be examined forever.
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            (fired if due <= now else keeping).append(item)
        if fired:
            _write_unlocked(keeping)
    return fired
