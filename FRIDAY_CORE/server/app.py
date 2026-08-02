# server/app.py
"""The ASGI app: /health for the HUD's pre-render probe, /ws for the turn stream.

Run with `python -m server.app` from FRIDAY_CORE (skill discovery needs
FRIDAY_CORE on sys.path, the same requirement core/main.py already has).

Concurrency shape, and why it looks like this:

`graph.stream()` is a synchronous generator and `core.llm_client.chat` blocks
on the network. Running a turn on the event loop would freeze every other
connection for the duration of the call, so a turn always runs in a worker
thread via `asyncio.to_thread`. `core.session.run_turn`'s `emit` callback
fires from that worker thread; events are marshalled back to the loop with
`loop.call_soon_threadsafe`, and the loop reference is captured *before* the
thread is spawned — `asyncio.get_running_loop()` called from inside the
worker raises, because a plain thread pool worker has no running loop of its
own.

Single-flight is enforced with a plain module-level flag, not a lock used to
serialise waiters. `pyttsx3` is not re-entrant (core/speaker.py already
swallows the `RuntimeError` from exactly that) and `InterruptHandler` is one
global flag — two concurrent turns would corrupt both. A second `prompt`
while one is in flight is rejected outright with an `error` event rather than
queued: a user who sends twice wants to be told, not silently delayed. An
`async with lock:` would have queued the second request instead of rejecting
it, so a check-then-set flag is used deliberately in its place — there is no
`await` between the check and the set, so it cannot race on a single-threaded
event loop.
"""
import asyncio
import ipaddress
import json
import platform
import queue
import threading

import uvicorn
from core.config import SETTINGS
from core.graph import build_graph
from core.registry import discover_skills
from core.session import SPOKEN_EVENT_TYPES, run_turn
from core.speaker import FridaySpeaker
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from server import events


class _Interrupter:
    """Settable interrupt flag consulted by core.session.run_turn.

    There is no physical Delete-key interrupt over a WebSocket connection;
    a `{"type": "cancel"}` message is the HUD's equivalent. `run_turn` only
    ever reads `.interrupted`, so this supplies the same read contract as
    core.interrupt_handler.InterruptHandler plus the two operations a
    socket needs: flipping the flag and resetting it.

    Reset happens at the start of every turn, not when a turn ends —
    run_turn deliberately leaves the flag set after an interrupted turn
    (see its docstring), and a cancel can also arrive between turns, while
    nothing is running at all. Either way the flag must not carry into the
    next prompt: cancelling turn N is not a request to also kill turn N+1.
    """

    def __init__(self):
        self.interrupted = False

    def cancel(self) -> None:
        self.interrupted = True

    def reset(self) -> None:
        self.interrupted = False


class _SpeechThread:
    """Owns the pyttsx3 engine on one dedicated, long-lived thread.

    pyttsx3 wraps SAPI5 on Windows, which is COM-based and thread-affine: the
    engine has to be driven from the same thread that created it. Verified
    directly on this machine, isolated from the rest of the server: an engine
    built on the main thread and then driven via `asyncio.to_thread` — which
    always hands the call to a pool thread distinct from the one that created
    it, even on the very first call — deadlocks in `engine.runAndWait()`. Not
    a raised error, not silence, a hang; the pool thread never returns. A
    dedicated thread with its own queue keeps the engine's home thread fixed
    no matter which pool thread the graph itself runs on for a given turn;
    callers just enqueue text.
    """

    def __init__(self, settings):
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(settings,), daemon=True)
        self._thread.start()

    def _run(self, settings):
        # A fresh OS thread has no COM apartment of its own. On the main
        # thread this happens implicitly (comtypes initialises it on first
        # use), which is exactly what made it easy to miss here — a plain
        # `threading.Thread` gets none of that, and pyttsx3.init() raises
        # "CoInitialize has not been called" without it. Confirmed directly:
        # dropping this line reproduces that exception on this machine.
        if platform.system() == "Windows":
            import comtypes
            comtypes.CoInitialize()
        try:
            speaker = FridaySpeaker(settings=settings)
            while True:
                text = self._queue.get()
                if text is None:
                    return
                speaker.speak(text)
        finally:
            if platform.system() == "Windows":
                import comtypes
                comtypes.CoUninitialize()

    def speak(self, text: str) -> None:
        self._queue.put(text)

    def stop(self) -> None:
        self._queue.put(None)


app = FastAPI()

active_skills = discover_skills()
graph = build_graph(active_skills)
interrupter = _Interrupter()
_speech = _SpeechThread(SETTINGS) if SETTINGS["server"]["speak"] else None

_connections: set[WebSocket] = set()
_memory_buffer: list[str] = []
_busy = False  # single-flight flag; see the module docstring for why not a lock
# Strong references to in-flight turn tasks. asyncio only holds a weak one, so
# a task nothing references can be garbage collected mid-run.
_turns: set[asyncio.Task] = set()


@app.get("/health")
async def health():
    # Electron polls this before rendering the HUD, so it must be cheap and
    # must never touch the model — skills were already discovered at import
    # time, this just reports what is already loaded.
    return {"status": "ok", "skills": sorted(active_skills)}


async def _broadcast(event: dict) -> None:
    """Fan one event out to every connected HUD window, dropping dead sockets."""
    text = json.dumps(event)
    dead = []
    for websocket in _connections:
        try:
            await websocket.send_text(text)
        except Exception:  # noqa: BLE001 — one dead socket must not break the others
            dead.append(websocket)
    for websocket in dead:
        _connections.discard(websocket)


async def _run_prompt(text: str) -> str:
    """Run one turn, streaming its events to every connected client as they arrive."""
    # A cancel can arrive between turns as well as during one: run_turn
    # leaves the flag set when it ends a turn early, and a cancel with no
    # turn in flight simply sets it with nothing to clear it. Resetting
    # here, at the start of this turn rather than the end of the last one,
    # is what keeps a stray cancel from also killing the next prompt.
    interrupter.reset()
    loop = asyncio.get_running_loop()
    queued: asyncio.Queue = asyncio.Queue()

    def emit(event_type: str, payload: dict) -> None:
        # Called from the worker thread run_turn executes on. The loop was
        # captured above, before that thread existed — calling
        # asyncio.get_running_loop() from inside the worker would raise,
        # since a plain thread pool worker has no loop of its own.
        loop.call_soon_threadsafe(queued.put_nowait, events.envelope(event_type, payload))
        if _speech is not None and event_type in SPOKEN_EVENT_TYPES:
            speak_text = payload.get("text")
            if speak_text:
                _speech.speak(speak_text)

    async def _drain() -> None:
        while True:
            event = await queued.get()
            if event is None:  # sentinel: the turn is over, whichever path it took
                return
            await _broadcast(event)

    drain_task = asyncio.create_task(_drain())
    history_length = SETTINGS["llm"]["history_length"]
    _memory_buffer.append(f"User: {text}")
    try:
        final_answer = await asyncio.to_thread(
            run_turn, graph, text, _memory_buffer, interrupter, emit, history_length,
        )
    except Exception as error:  # noqa: BLE001 — a failed turn must not close the socket
        # Anything the graph raises lands here: Ollama not running, an
        # unreachable llm.host once inference is offloaded, a skill throwing
        # past act_node's own guard. Left unhandled this propagated out of the
        # endpoint and tore the connection down with no message, so the HUD saw
        # a socket close and had nothing to display. Report it as an event and
        # keep the connection alive.
        final_answer = f"That request failed: {error}"
        queued.put_nowait(events.envelope(events.ERROR, {"text": final_answer}))
        # The turn never produced an answer, so drop the unpaired "User:" line
        # rather than leaving the transcript one-sided for the next prompt.
        _memory_buffer.pop()
    else:
        _memory_buffer.append(f"FRIDAY: {final_answer}")
        # Same trim policy as core/main.py: keep twice the read-back window so
        # the list does not grow unbounded across a long-lived process.
        del _memory_buffer[:-2 * history_length]
    finally:
        # The sentinel has to be posted on every path, or _drain awaits an
        # event that is never coming and the task leaks for the life of the
        # process.
        queued.put_nowait(None)
        await drain_task
    return final_answer


async def _run_and_release(text: str) -> None:
    """Run one turn and clear the single-flight flag however it ends."""
    global _busy
    try:
        await _run_prompt(text)
    finally:
        _busy = False


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    global _busy
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps(
                    events.envelope(events.ERROR, {"text": "Malformed message; expected JSON."})
                ))
                continue

            message_type = message.get("type")
            if message_type == "cancel":
                # Sets the flag; run_turn's own loop is what actually stops
                # the turn on its next streamed update. Nothing to send
                # back — the caller sees the turn end via a "status" event.
                interrupter.cancel()
                continue
            if message_type != "prompt":
                continue
            text = (message.get("text") or "").strip()
            if not text:
                continue

            if _busy:
                # Reject, do not queue — see the module docstring.
                await websocket.send_text(json.dumps(
                    events.envelope(events.ERROR, {"text": "Already processing a request. Try again shortly."})
                ))
                continue

            _busy = True
            # Run the turn as a task rather than awaiting it here. Awaiting
            # inline blocks this receive loop for the whole turn, so a second
            # prompt on the SAME connection just sits in the socket buffer and
            # runs when the first finishes — silently queued, which is the one
            # behaviour single-flight exists to prevent. A HUD is one
            # connection, so that was the case that mattered most. Handing the
            # turn to a task keeps this loop reading, so the next prompt hits
            # the busy flag and is rejected while the first is still running.
            _turns.add(task := asyncio.create_task(_run_and_release(text)))
            task.add_done_callback(_turns.discard)
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)


def _require_loopback(host: str) -> None:
    # This is a public repo shipping an agent with OS-automation skills. A
    # 0.0.0.0 default arriving later by accident — a copy-pasted config, a
    # container base image that rewrites host binding — is worth a two-line
    # gate rather than trusting the value blindly.
    if not ipaddress.ip_address(host).is_loopback:
        raise RuntimeError(f"refusing to bind server.host={host!r}: not a loopback address")


if __name__ == "__main__":
    _require_loopback(SETTINGS["server"]["host"])
    uvicorn.run(app, host=SETTINGS["server"]["host"], port=SETTINGS["server"]["port"])
