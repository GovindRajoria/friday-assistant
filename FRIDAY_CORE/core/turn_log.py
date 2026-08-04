# core/turn_log.py
"""What the last few turns actually did, kept so FRIDAY can be asked about it.

`explain_architecture` answers *how it works* from a document. This is the other
half: *what it just did*. Both exist for the same reason — asked to describe its
own behaviour from memory, the model produces a fluent and confident account of a
turn that never happened. So the account comes from a record written as the turn
ran.

**This is deliberately not the SQLite turn journal** described in the project's
own plan under "self-learning". That one is durable, answers "how often does it
pick the right tool", and is a measurement harness. This is an in-process ring
buffer that dies with the backend, and it exists only so `explain_last_turn` has
something true to read. Conflating the two would mean claiming a measurement
capability that does not exist yet.

Written from `core/session.py:run_turn`, which sees every node's delta already.
Nothing else writes to it.
"""
import threading
import time

# Small on purpose. This answers "what did you just do", not "what did you do on
# Tuesday" — and an unbounded list in a long-running backend is a slow leak.
MAX_TURNS = 10

_lock = threading.Lock()
_turns: list[dict] = []


def start(user_input: str) -> dict:
    """Open a record for a turn and return it. The caller passes it back in."""
    record = {
        "user_input": user_input,
        "started_at": time.time(),
        "started_display": time.strftime("%H:%M:%S"),
        "steps": [],
        "final_answer": "",
        "outcome": "in progress",
        "duration_seconds": None,
    }
    with _lock:
        _turns.append(record)
        del _turns[:-MAX_TURNS]
    return record


def step(record: dict | None, kind: str, **fields) -> None:
    """Append one step. `kind` is thought / action / observation / anomaly / refusal."""
    if record is None:
        return
    with _lock:
        record["steps"].append({"kind": kind, **fields})


def finish(record: dict | None, final_answer: str, outcome: str = "answered") -> None:
    if record is None:
        return
    with _lock:
        record["final_answer"] = final_answer
        record["outcome"] = outcome
        record["duration_seconds"] = round(time.time() - record["started_at"], 2)


def recent(count: int = 1) -> list[dict]:
    """The most recent `count` turns, newest last. Copies, so callers cannot mutate."""
    with _lock:
        return [dict(turn, steps=list(turn["steps"])) for turn in _turns[-max(1, count):]]


def clear() -> None:
    """Only for tests, and for a backend that wants a clean slate."""
    with _lock:
        _turns.clear()
