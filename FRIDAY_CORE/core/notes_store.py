# core/notes_store.py
"""Persisted JSON records for the task list and the journal.

Deliberately the same shape as `core/reminders.py`, and deliberately a separate
file. Tasks and reminders look similar and are not: a reminder is a *promise to
interrupt you at a time*, delivered by the scheduler thread whether or not anyone
is looking, while a task has no time and nothing delivers it — it sits until it is
done. Storing both in one file would mean the scheduler reading records it must
never fire on, which is exactly the kind of thing that fires at 3am once.

JSON rather than the SQLite vault, for the reasons `reminders.py` gives: a handful
of records, and a file the operator can open in an editor is worth more here than a
query language nobody will use. Git-ignored, because the contents are whatever they
wrote down.

Timestamps are stored as UTC ISO-8601 with an explicit offset. A journal is read
back by date, and a naive local timestamp puts an entry on the wrong day twice a
year.
"""
import json
import threading
import uuid
from datetime import datetime, timezone

from core.config import CONFIG_DIR

# Low enough that a runaway loop calling a skill cannot fill the disk; high
# enough that neither is a real constraint in ordinary use.
MAX_RECORDS = {"tasks": 300, "journal": 2000}

_lock = threading.Lock()


def store_path(kind: str):
    return CONFIG_DIR / f"{kind}.json"


def _read_unlocked(kind: str) -> list[dict]:
    try:
        raw = store_path(kind).read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Same reasoning as reminders.py: a corrupt file must not raise on every
        # call. Returning empty loses records, which is bad; raising forever is
        # worse, and the file is still there to be repaired by hand.
        return []
    return data if isinstance(data, list) else []


def _write_unlocked(kind: str, records: list[dict]) -> None:
    path = store_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def load(kind: str) -> list[dict]:
    with _lock:
        return _read_unlocked(kind)


def add(kind: str, fields: dict) -> dict:
    """Append one record, stamped and given an id. Returns the stored record."""
    record = {
        "id": uuid.uuid4().hex[:8],
        "created": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    limit = MAX_RECORDS.get(kind, 500)
    with _lock:
        records = _read_unlocked(kind)
        if len(records) >= limit:
            raise ValueError(f"there are already {limit} {kind} records stored")
        records.append(record)
        _write_unlocked(kind, records)
    return record


def update(kind: str, record_id: str, changes: dict) -> dict | None:
    with _lock:
        records = _read_unlocked(kind)
        for record in records:
            if record.get("id") == record_id:
                record.update(changes)
                _write_unlocked(kind, records)
                return dict(record)
    return None


def remove(kind: str, record_id: str) -> bool:
    with _lock:
        records = _read_unlocked(kind)
        remaining = [record for record in records if record.get("id") != record_id]
        if len(remaining) == len(records):
            return False
        _write_unlocked(kind, remaining)
    return True


def local_day(iso_timestamp: str) -> str:
    """The local calendar date of a stored UTC timestamp, as YYYY-MM-DD."""
    try:
        moment = datetime.fromisoformat(iso_timestamp)
    except (TypeError, ValueError):
        return "unknown"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d")
