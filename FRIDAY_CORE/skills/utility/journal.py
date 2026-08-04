# skills/utility/journal.py
"""Append a dated note; read a day or a week back.

Feeds the question "what did I do last Tuesday", which nothing in this project
could previously answer. Two things keep it honest.

**It never summarises on write.** The note is stored as it was said. A journal whose
entries have been through a paraphrase is not a record of anything, and the whole
value here is that next week's answer is what actually happened.

**Reading back is bounded and dated.** Entries come back grouped under their local
calendar date, oldest first, so the reply reads as a record rather than a pile. It
returns what it has rather than filling gaps: a day with no entries says so, which
matters because the alternative is a model inventing a plausible Tuesday.
"""
from datetime import datetime, timedelta, timezone

from core import notes_store

KIND = "journal"
MAX_ENTRIES_RETURNED = 40


class JournalSkill:
    def __init__(self):
        self.manifest = {
            "name": "journal",
            "description": (
                "Records dated notes about what happened and reads them back later. "
                "Parameters: 'action' (add, read), 'text' for the note, and 'when' for "
                "reading — 'today', 'yesterday', 'week', or a date like 2026-08-01. Use this "
                "when the user wants to record what they did or ask what they did on some "
                "past day. Use task_list for things still to be done, and manage_memory for "
                "facts to remember rather than events."
            ),
            "parameters": ["action", "text", "when"],
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "add").lower()

        if action in {"add", "write", "record", "log", "note"}:
            return self._add(params.get("text"))
        if action in {"read", "show", "list", "recall", "what"}:
            return self._read(params.get("when"))
        return {"status": "error", "message": f"Unknown journal action '{action}'. Use add or read."}

    def _add(self, text):
        text = str(text or "").strip()
        if not text:
            return {"status": "error", "message": "What should I write in the journal?"}
        try:
            record = notes_store.add(KIND, {"text": text})
        except (ValueError, OSError) as error:
            return {"status": "error", "message": f"I could not write that down: {error}"}
        day = notes_store.local_day(record["created"])
        return {
            "status": "success",
            "message": f"Written to the journal for {day}.",
            "data": {"id": record["id"], "day": day},
        }

    def _read(self, when):
        wanted_days, label = self._resolve_days(when)
        records = notes_store.load(KIND)
        if not records:
            return {"status": "success", "message": "The journal is empty.", "data": {"entries": 0}}

        matching = [record for record in records
                    if notes_store.local_day(record.get("created", "")) in wanted_days]
        if not matching:
            return {
                "status": "success",
                "message": f"Nothing is written in the journal for {label}.",
                "data": {"entries": 0},
            }

        by_day = {}
        for record in sorted(matching, key=lambda r: r.get("created", "")):
            by_day.setdefault(notes_store.local_day(record["created"]), []).append(record)

        lines = []
        shown = 0
        for day in sorted(by_day):
            lines.append(f"{day}:")
            for record in by_day[day]:
                if shown >= MAX_ENTRIES_RETURNED:
                    break
                lines.append(f"  {self._time_of(record)} {record.get('text', '')}")
                shown += 1
        if len(matching) > shown:
            lines.append(f"({len(matching) - shown} more entries not shown.)")

        return {
            "status": "success",
            "message": f"Journal for {label} — {len(matching)} entry(s):\n" + "\n".join(lines),
            "data": {"entries": len(matching), "days": len(by_day)},
        }

    @staticmethod
    def _time_of(record):
        try:
            moment = datetime.fromisoformat(record.get("created", ""))
        except (TypeError, ValueError):
            return "     "
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%H:%M")

    @staticmethod
    def _resolve_days(when):
        """(set of YYYY-MM-DD, human label). Defaults to today."""
        today = datetime.now().astimezone().date()
        text = str(when or "today").strip().lower()

        if text in {"today", ""}:
            return {today.isoformat()}, "today"
        if text in {"yesterday"}:
            day = today - timedelta(days=1)
            return {day.isoformat()}, "yesterday"
        if text in {"week", "this week", "last week", "7 days", "past week"}:
            days = {(today - timedelta(days=offset)).isoformat() for offset in range(7)}
            return days, "the last seven days"
        if text in {"month", "this month", "past month", "30 days"}:
            days = {(today - timedelta(days=offset)).isoformat() for offset in range(30)}
            return days, "the last thirty days"
        if text in {"all", "everything", "ever"}:
            # Every day the store could possibly hold, rather than a special case
            # downstream: bounded by MAX_RECORDS, so this stays small.
            return {notes_store.local_day(record.get("created", ""))
                    for record in notes_store.load(KIND)}, "the whole journal"

        # An explicit date, in any of the three orders a person might say it.
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(text, fmt).date()
                return {parsed.isoformat()}, parsed.strftime("%d %b %Y")
            except ValueError:
                continue
        # Unrecognised: today, and the label says what was assumed rather than
        # silently answering a different question.
        return {today.isoformat()}, f"today (I did not understand '{when}')"


def setup():
    return JournalSkill()
