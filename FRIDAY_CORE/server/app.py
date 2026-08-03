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
from contextlib import asynccontextmanager
from datetime import datetime, time

import uvicorn
from core.config import SETTINGS
from core.graph import build_graph
from core.registry import discover_skills
from core.session import SPOKEN_EVENT_TYPES, run_turn
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


class _PendingConfirmation:
    """Bridges the turn's worker thread and the event loop that answers it.

    `core.nodes.confirm.confirm_node` calls the `confirm` callable
    synchronously, from `asyncio.to_thread`'s worker — a plain thread with no
    event loop of its own, the same reason `_Interrupter` above is a plain
    flag rather than an asyncio primitive. threading.Event is what a thread
    with no loop can actually block on; the WebSocket handler runs on the
    loop and sets it when a `{"type": "confirm", ...}` message arrives.

    Module-level, not per-turn: the server is single-flight (one turn at a
    time — see the module docstring), so there is only ever at most one
    pending confirmation to track.
    """

    def __init__(self):
        self._event = threading.Event()
        self._approved = False

    def reset(self) -> None:
        # Cleared at the start of every confirmation wait, not just once at
        # startup — a stale approval left over from resolve() would
        # otherwise let a later, unrelated destructive action sail through
        # without ever actually blocking.
        self._event.clear()
        self._approved = False

    def resolve(self, approved: bool) -> None:
        self._approved = approved
        self._event.set()

    def wait(self, timeout: float) -> bool:
        # A timed-out wait denies. Without this, an operator who never
        # answers — the HUD closed, they stepped away — would block this
        # thread, and therefore single-flight, for the rest of the process.
        got_answer = self._event.wait(timeout=timeout)
        return got_answer and self._approved


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
            # Imported here rather than at module scope so that importing this
            # module does not require pyttsx3. CI installs neither
            # requirements.txt nor an audio device, and the tests run with
            # server.speak off, so this thread never starts there — but a
            # module-level import would still have made the whole test file
            # uncollectable. Same reason core/llm_client.py imports ollama
            # inside get_client().
            from core.speaker import FridaySpeaker

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


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Forward-references the start/stop helpers defined further down — fine,
    # since none of them run until uvicorn actually starts serving, well after
    # the whole module has finished loading.
    _start_speech()
    _start_transcriber()
    await _start_screen_watcher()
    await _start_scheduler()
    yield
    await _stop_scheduler()
    await _stop_screen_watcher()
    _stop_speech()


app = FastAPI(lifespan=_lifespan)

# Deny-by-timeout budget for a destructive action nobody answers. 60s is
# generous for a human actually looking at the HUD and stingy enough that a
# closed HUD or an absent operator does not wedge single-flight indefinitely.
# Read as a bare module global inside _confirm_via_socket below, not as a
# bound default argument — a default is captured once at function-definition
# time, which would make `monkeypatch.setattr(server_app,
# "CONFIRMATION_TIMEOUT_SECONDS", ...)` silently no-op in tests.
CONFIRMATION_TIMEOUT_SECONDS = 60

_pending_confirmation = _PendingConfirmation()
# Set at the start of every turn in _run_prompt, to that turn's `emit`. The
# graph is built once, at module scope, long before any turn exists, so the
# `confirm` callable it holds cannot close over a turn-specific emit
# directly — this module-level indirection is what lets one graph object
# serve every turn without knowing anything about turns or sockets itself.
_current_emit = None


def _confirm_via_socket(action: str, action_input: dict, thought: str) -> bool:  # noqa: ARG001 — thought kept for parity with the confirm signature; unused here
    """The `confirm` callable wired into build_graph for the WebSocket server.

    Runs on the turn's worker thread (core.nodes.confirm calls it
    synchronously). Surfaces `confirmation_required` to every connected HUD
    through the current turn's `emit`, then blocks until the WebSocket
    handler resolves `_pending_confirmation` — from a `{"type": "confirm",
    "approved": ...}` message — or the timeout above denies it outright.
    """
    _pending_confirmation.reset()
    if _current_emit is not None:
        _current_emit("confirmation_required", {"name": action, "input": action_input})
    return _pending_confirmation.wait(CONFIRMATION_TIMEOUT_SECONDS)


active_skills = discover_skills()
graph = build_graph(active_skills, confirm=_confirm_via_socket)
interrupter = _Interrupter()

# Started by the lifespan handler, not at import. Building it here meant
# importing this module spawned a thread that immediately tried to load
# pyttsx3, which then died with an ImportError anywhere without audio — CI,
# and the subprocess in tests/test_imports_without_runtime_deps.py, both of
# which mask that package deliberately. The thread failing was harmless in
# itself, but a module that starts a thread merely by being imported races
# with interpreter shutdown and prints a traceback that belongs to nothing.
_speech: "_SpeechThread | None" = None


def _start_speech() -> None:
    global _speech
    if _speech is None and SETTINGS["server"]["speak"]:
        _speech = _SpeechThread(SETTINGS)


def _stop_speech() -> None:
    global _speech
    if _speech is not None:
        _speech.stop()
        _speech = None

# Speech to text for the HUD's microphone. Loading the model is seconds of
# work — more the first time, when the weights are being fetched — so it
# happens on a worker thread and nothing waits for it here. /health must stay
# instant: Electron will not show the window until it answers.
_transcriber_task: "asyncio.Task | None" = None


def _start_transcriber() -> None:
    global _transcriber_task
    if not SETTINGS["audio"].get("stt_enabled", True):
        return
    _transcriber_task = asyncio.create_task(asyncio.to_thread(_build_transcriber))


def _build_transcriber():
    # Imported inside the worker, not at module scope: faster_whisper is
    # absent in CI and masked outright by
    # tests/test_imports_without_runtime_deps.py, which imports this module.
    from core.transcriber import Transcriber

    return Transcriber(SETTINGS)


async def _transcribe(audio: bytes) -> str:
    """Text for one recorded utterance, or "" if nothing was said.

    Awaiting the startup task is what makes a button press during a cold
    start wait for the model rather than fail against a half-built one. A
    task can be awaited any number of times; every later press resolves
    immediately off the finished result.
    """
    # Ordinarily the lifespan handler has already started this at boot, so the
    # first press pays nothing. Starting it here too means the load is
    # triggered by whoever needs it first rather than depending on startup
    # having run — which is also what makes this reachable under
    # `TestClient(app).websocket_connect(...)`, the form used throughout
    # tests/test_server.py, where no lifespan runs at all.
    if _transcriber_task is None:
        _start_transcriber()
    if _transcriber_task is None:
        raise RuntimeError("speech recognition is disabled (audio.stt_enabled is false)")
    transcriber = await _transcriber_task
    return await asyncio.to_thread(transcriber.transcribe, audio)


_connections: set[WebSocket] = set()
_memory_buffer: list[str] = []
_busy = False  # single-flight flag; see the module docstring for why not a lock
# Strong references to in-flight turn tasks. asyncio only holds a weak one, so
# a task nothing references can be garbage collected mid-run.
_turns: set[asyncio.Task] = set()
# Same reason as _turns: asyncio holds only a weak reference, so a
# transcription nothing else references can be collected mid-run.
_utterances: set[asyncio.Task] = set()

# vision.watcher.ScreenWatcher instance, created at startup only when
# screen.enabled — stays None otherwise, which is what makes "screen.enabled:
# false" a true no-op: nothing below imports vision at all in that case.
_watcher = None
_screen_events: "asyncio.Queue | None" = None
_screen_drain_task: "asyncio.Task | None" = None


@app.get("/health")
async def health():
    # Electron polls this before rendering the HUD, so it must be cheap and
    # must never touch the model — skills were already discovered at import
    # time, this just reports what is already loaded.
    return {"status": "ok", "skills": sorted(active_skills)}


async def _start_screen_watcher() -> None:
    """Start continuous screen awareness, only when the operator opted in.

    mss and PIL are imported here, not at module scope — neither is
    installed in CI (tests/test_imports_without_runtime_deps.py masks both),
    and this module sits in the import chain that test exercises. Same
    pattern as core.speaker inside _SpeechThread._run above: a module-scope
    import here would make the whole server uncollectable on the runner.
    """
    if not SETTINGS["screen"]["enabled"]:
        return

    from vision.describers import get_describer
    from vision.watcher import ScreenWatcher

    global _watcher, _screen_events, _screen_drain_task
    _screen_events = asyncio.Queue()
    _screen_drain_task = asyncio.create_task(_drain_screen_events())

    loop = asyncio.get_running_loop()
    # `lambda: _busy` reads the module global at call time, not at
    # definition time — it sees whatever _run_and_release has most recently
    # set. That is the "is a turn running" signal the watcher needs to skip
    # a cycle instead of contending with an in-flight turn for the same
    # Ollama process (see vision/watcher.py's docstring for the measured
    # cost of not doing this).
    #
    # on_error exists because the watcher runs entirely off this event loop;
    # a describe failure against a remote vlm.host had nowhere to surface
    # before this, so the HUD's screen context would just go stale with
    # nothing telling anyone why. The queue carries a (kind, text) pair
    # rather than bare text so _drain_screen_events can tell a description
    # from a failure and route each to the right wire event.
    _watcher = ScreenWatcher(
        SETTINGS, get_describer(SETTINGS), is_busy=lambda: _busy,
        on_error=lambda error: loop.call_soon_threadsafe(_screen_events.put_nowait, ("error", str(error))),
    )
    _watcher.start(
        on_description=lambda text: loop.call_soon_threadsafe(_screen_events.put_nowait, ("description", text))
    )


def _screen_event_envelope(kind: str, text: str) -> dict:
    """Map one item off the watcher's queue to a wire event.

    A failure reuses the existing `error` type rather than adding a new one
    — the vocabulary drift gate (tests/test_event_vocabulary_drift.py) gates
    server/events.py against desktop/src/events.ts, and `error` is already
    the honest fit: a describe failure is exactly the same kind of thing to
    the HUD as a failed turn, just arriving from the watcher thread instead
    of _run_prompt. Split out of _drain_screen_events so the mapping is
    testable without an event loop.
    """
    if kind == "error":
        return events.envelope(events.ERROR, {"text": f"Screen awareness: {text}"})
    return events.envelope(events.SCREEN_CONTEXT, {"text": text})


async def _drain_screen_events() -> None:
    """Fan the watcher's descriptions and failures out to every connected HUD.

    The watcher runs on its own thread and has no socket of its own; this
    task is the bridge, the same role _drain plays for a single turn in
    _run_prompt below, just for the lifetime of the process instead of one
    turn.
    """
    while True:
        kind, text = await _screen_events.get()
        await _broadcast(_screen_event_envelope(kind, text))


_scheduler = None
_proactive_events = None
_proactive_drain_task = None


async def _start_scheduler() -> None:
    """Start reminders and the daily briefing, only when the operator opted in."""
    if not SETTINGS.get("proactive", {}).get("enabled", False):
        return

    from core.scheduler import Scheduler

    global _scheduler, _proactive_events, _proactive_drain_task
    _proactive_events = asyncio.Queue()
    _proactive_drain_task = asyncio.create_task(_drain_proactive_events())

    loop = asyncio.get_running_loop()
    # Everything crosses to the loop before any decision is made about it.
    # The scheduler thread cannot read _busy safely — a turn can start
    # between a check on that thread and the emit that follows it — so the
    # thread's only job is to hand over (kind, text) and the loop decides.
    _scheduler = Scheduler(
        SETTINGS, active_skills,
        on_event=lambda kind, text: loop.call_soon_threadsafe(_proactive_events.put_nowait, (kind, text)),
        on_error=lambda error: loop.call_soon_threadsafe(
            _proactive_events.put_nowait, ("error", str(error))),
    )
    _scheduler.start()


async def _stop_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.stop()
    if _proactive_drain_task is not None:
        _proactive_drain_task.cancel()


def _proactive_envelope(kind: str, text: str) -> dict:
    """Map one scheduler item to a wire event.

    A scheduler failure reuses `error`, exactly as the watcher's does — it is
    the same kind of thing to the HUD, just arriving from a different thread.
    """
    if kind == "error":
        return events.envelope(events.ERROR, {"text": f"Scheduler: {text}"})
    return events.envelope(events.PROACTIVE, {"text": text})


async def _drain_proactive_events() -> None:
    """Deliver reminders and briefings to every connected HUD, and speak them.

    Deliberately does NOT touch `_busy`. A proactive message is not a turn:
    setting the flag would make the operator's very next prompt bounce off
    "Already processing a request", and clearing it could release a real turn
    that is still running. It also stays out of `_memory_buffer` — that is
    the conversational transcript fed into the next prompt, and a briefing
    injected there becomes something the model believes the operator said.
    """
    while True:
        kind, text = await _proactive_events.get()
        # Re-read on the loop, where it is actually coherent. If a turn is in
        # flight the message waits for the next tick rather than interleaving
        # with the answer the operator is reading.
        if kind != "error" and _busy:
            continue
        await _broadcast(_proactive_envelope(kind, text))
        if kind != "error":
            _speak_proactive(text)


def _speak_proactive(text: str) -> None:
    """Say it out loud, unless it is the middle of the night.

    Quiet hours gate the voice and nothing else — the message has already
    been broadcast by the time this runs, so a 03:00 briefing is silently
    waiting in the HUD rather than missing.
    """
    if _speech is None:
        return
    from core.scheduler import in_quiet_hours, parse_hhmm

    config = SETTINGS.get("proactive", {})
    start = parse_hhmm(config.get("quiet_start", "22:00"), time(22, 0))
    end = parse_hhmm(config.get("quiet_end", "07:00"), time(7, 0))
    if in_quiet_hours(datetime.now().time(), start, end):
        return
    _speech.speak(text)


async def _stop_screen_watcher() -> None:
    if _watcher is not None:
        _watcher.stop()
    if _screen_drain_task is not None:
        _screen_drain_task.cancel()


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
    global _current_emit
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
    # "" when the watcher never started (screen.enabled: false, the
    # default) — run_turn's screen_context parameter already defaults the
    # same way, this just makes the no-watcher case explicit here too.
    screen_context = _watcher.latest_description if _watcher is not None else ""
    _memory_buffer.append(f"User: {text}")
    # Published before the worker thread starts, so _confirm_via_socket can
    # reach this turn's emit the moment the graph routes to "confirm" — see
    # that function's docstring for why this indirection exists at all.
    _current_emit = emit
    try:
        final_answer = await asyncio.to_thread(
            run_turn, graph, text, _memory_buffer, interrupter, emit, history_length, screen_context,
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
        _current_emit = None
    return final_answer


async def _run_and_release(text: str) -> None:
    """Run one turn and clear the single-flight flag however it ends."""
    global _busy
    try:
        await _run_prompt(text)
    finally:
        _busy = False


async def _handle_utterance(audio: bytes) -> None:
    """Transcribe one recorded utterance and report what was heard.

    It reports; it does not run. The text is broadcast as a `transcript`
    event and lands in the HUD's prompt box, where the operator reads it and
    presses send. Feeding it straight into the graph would be one keystroke
    shorter and would mean a misheard sentence becomes a request this
    assistant acts on — and the actions available to it include deleting
    files. Recognition being good is the reason that rarely happens; the
    review step is the reason it does not matter when it does.

    Not gated on `_busy` either. Hearing is not a turn: transcribing while an
    answer is still streaming is fine, and the operator can line up their
    next sentence while they wait.
    """
    try:
        text = await _transcribe(audio)
    except Exception as error:  # noqa: BLE001 — a failed transcription must not close the socket
        # Covers the model failing to load at all (no faster-whisper, no disk
        # space for the weights, stt_enabled off) as well as a blob that
        # would not decode. All of them are things the operator can act on
        # once they can see them, and none are worth dropping the connection.
        await _broadcast(events.envelope(events.ERROR, {"text": f"Could not transcribe that: {error}"}))
        return

    if not text:
        # The VAD found no speech. Saying so matters: without it, pressing
        # the button and releasing it too early looks identical to the
        # microphone being broken.
        await _broadcast(events.envelope(events.STATUS, {"text": "I did not catch that."}))
        return

    await _broadcast(events.envelope(events.TRANSCRIPT, {"text": text}))


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    global _busy
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            # receive(), not receive_text(): the HUD's microphone sends the
            # recorded utterance as a binary frame on this same socket. It is
            # the only thing that ever sends binary, so the frame needs no
            # header of its own — its type is the discriminator.
            #
            # A separate HTTP endpoint would have been the obvious home for an
            # audio upload, but the renderer cannot reach one. A fetch from
            # the renderer to 127.0.0.1:8756 is cross-origin, and allowing it
            # means CORS headers on a server with no authentication, which
            # hands the whole surface to any web page the operator visits.
            # The WebSocket is already open and already exempt from that.
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            audio = message.get("bytes")
            if audio is not None:
                # A task, not an inline await, for exactly the reason the
                # prompt below is a task: awaiting here blocks this receive
                # loop for the whole transcription, and the HUD is one
                # connection. That would leave it unable to cancel, unable to
                # answer a pending confirmation and unable to send anything
                # for two seconds on a warm model — or for the length of a
                # 460 MB download on the very first press after an install.
                _utterances.add(job := asyncio.create_task(_handle_utterance(audio)))
                job.add_done_callback(_utterances.discard)
                continue

            raw = message.get("text")
            if raw is None:
                continue
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
            if message_type == "confirm":
                # Resolves whichever destructive action is currently
                # blocking the worker thread in _confirm_via_socket. A
                # confirm arriving with nothing pending (no turn running, or
                # the turn already timed out) just sets a flag nothing is
                # waiting on — harmless, and cheaper than tracking whether a
                # wait is actually in flight.
                _pending_confirmation.resolve(bool(message.get("approved")))
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
