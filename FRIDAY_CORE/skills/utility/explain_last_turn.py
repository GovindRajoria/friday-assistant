# skills/utility/explain_last_turn.py
"""What it just did, from the record rather than from the model's memory.

`explain_architecture` answers "how do you work" out of a document, because asked
that question from memory the model described "a graph-based natural language
processing library for reasoning about relationships between concepts" — fluent,
confident, wrong in every particular. This is the same fix applied to a different
question: "what did you just do", "why did you refuse that", "which tool did you
use".

A model asked to recount its own previous turn will produce a coherent narrative
of steps it did not take. So the steps come from `core/turn_log.py`, written as
the turn ran, and this skill formats them without asking the model to interpret
anything.

Terminal, for the same reason as its sibling: the record is the answer.

Honest about its own limit — the log is in-process. A restarted backend has
nothing to report, and it says so rather than inventing a plausible last turn.
"""
from core import turn_log

MAX_OBSERVATION_CHARS = 300


class ExplainLastTurnSkill:
    def __init__(self):
        self.manifest = {
            "name": "explain_last_turn",
            "description": (
                "Reports exactly what you did on a previous turn: each thought, which "
                "tools you called with what parameters, what they returned, anything that "
                "was refused, and how long it took. Use this when asked what you just did, "
                "which tool you used, why something was refused, or why a request failed. "
                "Parameters: optionally 'count' for how many recent turns. Its answer is "
                "complete — the turn ends when it returns. Use explain_architecture for how "
                "you work in general rather than what happened just now."
            ),
            "parameters": ["count"],
            "terminal": True,
        }

    def execute(self, params=None):
        params = params or {}
        try:
            count = max(1, min(int(params.get("count") or 1), turn_log.MAX_TURNS))
        except (TypeError, ValueError):
            count = 1

        # The current turn is in the log too — this skill is running inside it —
        # so it is dropped before counting back. Reporting it would tell the
        # operator only that they had just asked what happened.
        history = [turn for turn in turn_log.recent(count + 1)][:-1]
        if not history:
            return {
                "status": "success",
                "message": ("I have no record of an earlier turn. Either this is the first "
                            "thing asked since the backend started, or it was restarted since "
                            "— the record is kept in memory and does not survive that."),
                "data": {"turns": 0},
            }

        blocks = [self._render(turn) for turn in reversed(history[-count:])]
        return {
            "status": "success",
            "message": "\n\n".join(blocks),
            "data": {"turns": len(blocks)},
        }

    def _render(self, turn: dict) -> str:
        lines = [f"At {turn['started_display']} you asked: \"{turn['user_input']}\""]

        if not turn["steps"]:
            lines.append("  I took no steps — I answered directly.")
        for index, step in enumerate(turn["steps"], start=1):
            lines.append(f"  {index}. {self._render_step(step)}")

        duration = turn.get("duration_seconds")
        timing = f" in {duration}s" if duration is not None else ""
        outcome = turn.get("outcome", "")
        if outcome and outcome != "answered":
            lines.append(f"  Outcome: {outcome}{timing}.")
        elif turn.get("final_answer"):
            lines.append(f"  I answered{timing}: \"{self._clip(turn['final_answer'])}\"")
        return "\n".join(lines)

    def _render_step(self, step: dict) -> str:
        kind = step.get("kind")
        if kind == "thought":
            return f"Thought: {self._clip(step.get('text', ''))}"
        if kind == "action":
            params = step.get("input") or {}
            rendered = ", ".join(f"{k}={v!r}" for k, v in params.items()) or "no parameters"
            return f"Called {step.get('name')} with {rendered}"
        if kind == "observation":
            return f"It returned: {self._clip(step.get('text', ''))}"
        if kind == "confirmation":
            if step.get("approved"):
                return "You approved the action at the confirmation gate."
            return f"Refused at the confirmation gate: {self._clip(step.get('text', ''))}"
        if kind == "anomaly":
            return f"Privacy guard: {self._clip(step.get('text', ''))}"
        return f"{kind}: {self._clip(str(step))}"

    @staticmethod
    def _clip(text: str) -> str:
        text = " ".join(str(text).split())
        return text if len(text) <= MAX_OBSERVATION_CHARS else text[:MAX_OBSERVATION_CHARS - 3] + "..."


def setup():
    return ExplainLastTurnSkill()
