# skills/utility/task_list.py
"""A persistent to-do list, distinct from timed reminders.

The distinction is the whole point and the description below works hard to make it
routable, because "remind me to call the bank" and "add call the bank to my list"
are the same sentence to a language model and different things to a person. A
reminder interrupts you at a time. A task waits until it is done, and nothing
delivers it.

Sharing a store with reminders was rejected: `core/scheduler.py` reads that file on
a timer, and records with no due time sitting in it are how something fires at 3am
once.
"""
from core import notes_store

KIND = "tasks"
MAX_LISTED = 30


def _own_records():
    """Records belonging to this skill.

    tasks.json is shared with track_price, which tags its records. Filtering on
    the absence of a tag is what keeps a watched web page out of the to-do list.
    Caught by a test: track_price filtered its own records correctly and this
    skill did not, so a price watch appeared as an outstanding task — the exact
    unenforced-convention bug that sharing a store invites.
    """
    return [record for record in notes_store.load(KIND) if not record.get("tag")]


class TaskListSkill:
    def __init__(self):
        self.manifest = {
            "name": "task_list",
            "description": (
                "Keeps a lasting to-do list: add a task, list what is outstanding, mark one "
                "done, or remove it. Parameters: 'action' (add, list, done, remove), 'text' "
                "for the task, and 'id' for done and remove. Use this for something that has "
                "to be done but has no particular time — use reminders instead when the user "
                "wants to be interrupted at a specific time or after a delay."
            ),
            "parameters": ["action", "text", "id"],
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "list").lower()

        if action in {"add", "new", "create"}:
            return self._add(params.get("text"))
        if action in {"done", "complete", "completed", "finish", "finished", "tick"}:
            return self._done(params.get("id"), params.get("text"))
        if action in {"remove", "delete", "drop"}:
            return self._remove(params.get("id"))
        if action in {"list", "show", "all", "outstanding"}:
            return self._list(include_done=action == "all")
        return {"status": "error",
                "message": f"Unknown task action '{action}'. Use add, list, done or remove."}

    def _add(self, text):
        text = str(text or "").strip()
        if not text:
            return {"status": "error", "message": "What should I add to the list?"}
        try:
            record = notes_store.add(KIND, {"text": text, "done": False})
        except (ValueError, OSError) as error:
            return {"status": "error", "message": f"I could not save that task: {error}"}
        return {
            "status": "success",
            "message": f"Added to your list: {text} (id {record['id']})",
            "data": {"id": record["id"]},
        }

    def _list(self, include_done=False):
        records = _own_records()
        outstanding = [record for record in records if not record.get("done")]
        completed = [record for record in records if record.get("done")]

        if not records:
            return {"status": "success", "message": "Your task list is empty.",
                    "data": {"outstanding": 0}}
        if not outstanding and not include_done:
            return {
                "status": "success",
                "message": f"Nothing outstanding — all {len(completed)} task(s) are done.",
                "data": {"outstanding": 0, "done": len(completed)},
            }

        lines = [f"  {record['id']}: {record.get('text', '(no text)')}"
                 for record in outstanding[:MAX_LISTED]]
        message = f"{len(outstanding)} task(s) outstanding:\n" + "\n".join(lines)
        if include_done and completed:
            done_lines = [f"  {record['id']}: {record.get('text', '')} (done)"
                          for record in completed[:MAX_LISTED]]
            message += f"\n{len(completed)} completed:\n" + "\n".join(done_lines)
        elif completed:
            message += f"\n({len(completed)} completed, not shown.)"
        return {"status": "success", "message": message,
                "data": {"outstanding": len(outstanding), "done": len(completed)}}

    def _done(self, record_id, text):
        record_id = str(record_id or "").strip()
        if not record_id and text:
            # The model tends to repeat the task's words rather than its id, and
            # refusing that is needlessly pedantic when the match is unambiguous.
            record_id = self._match_by_text(str(text))
            if record_id is None:
                return {"status": "error",
                        "message": f"I could not find one clear task matching '{text}'. Ask me to list them."}
        if not record_id:
            return {"status": "error", "message": "Which task? Ask me to list them first."}

        # Ownership checked before writing, not just when listing: an id belonging
        # to a price watch must not be markable done through the task list.
        if not any(record["id"] == record_id for record in _own_records()):
            return {"status": "error", "message": f"There is no task with id {record_id}."}

        updated = notes_store.update(KIND, record_id, {"done": True})
        if updated is None:
            return {"status": "error", "message": f"There is no task with id {record_id}."}
        remaining = len([r for r in _own_records() if not r.get("done")])
        return {
            "status": "success",
            "message": (f"Marked done: {updated.get('text', '')}. "
                        f"{remaining} task(s) still outstanding."),
            "data": {"id": record_id, "outstanding": remaining},
        }

    def _remove(self, record_id):
        record_id = str(record_id or "").strip()
        if not record_id:
            return {"status": "error", "message": "Which task should I remove? Ask me to list them."}
        if not any(record["id"] == record_id for record in _own_records()):
            return {"status": "error", "message": f"There is no task with id {record_id}."}
        if notes_store.remove(KIND, record_id):
            return {"status": "success", "message": f"Removed task {record_id}.",
                    "data": {"id": record_id}}
        return {"status": "error", "message": f"There is no task with id {record_id}."}

    @staticmethod
    def _match_by_text(text):
        """The single outstanding task whose text contains this, or None if ambiguous."""
        needle = text.strip().lower()
        if not needle:
            return None
        matches = [record for record in _own_records()
                   if not record.get("done") and needle in str(record.get("text", "")).lower()]
        return matches[0]["id"] if len(matches) == 1 else None


def setup():
    return TaskListSkill()
