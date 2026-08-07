# FRIDAY

[![CI](https://github.com/GovindRajoria/friday-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/GovindRajoria/friday-assistant/actions/workflows/ci.yml)

A voice-driven desktop assistant that runs entirely on local hardware. Speech recognition, reasoning, and text-to-speech all execute on-device — there is no cloud API in the loop, and no audio or camera data leaves the machine.

The interesting part is not the voice interface; it is the reasoning loop. FRIDAY runs a **LangGraph state machine** on top of a local Llama 3.1 via Ollama: every decision is structured output against a JSON schema, not parsed free text, so it can chain several tools together to answer one request — search the web, do arithmetic internally, write the result to a document, then commit a note to long-term memory — deciding each step from the outcome of the last one, with a real bound on how long a chain can run.

The graph is also where the safety properties live. Anything destructive routes
through a node that shows a human the exact call and waits, and denies by
default. That is an edge in a state machine, not a sentence in a prompt asking
the model to be careful.

It also knows how it is built: asked how it works, it reads
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and answers from the file rather
than from whatever the model remembers about assistants in general.

Built May 2026.

---

## How it works

```mermaid
flowchart LR
  mic(["microphone<br/>console"]) --> stt["faster-whisper<br/>wake word + STT"]
  hud(["microphone<br/>HUD — Ctrl+Shift+Space"]) --> rec["MediaRecorder<br/>WebM/Opus over /ws"]
  rec --> stt2["faster-whisper<br/>same model, no wake word"]
  wake(["microphone<br/>HUD — always listening"]) --> seg["continuous record,<br/>cut on silence"]
  seg --> stt3["faster-whisper"]
  stt3 --> gate{"core/wake_word.py<br/>addressed by name?"}
  gate -->|"no"| drop(["discarded"])
  kbd(["keypress"]) --> typed["typed input"]

  stt --> entry
  stt2 -->|"the press is the address"| entry
  typed --> entry
  gate -->|"yes — name stripped"| entry

  subgraph graph["core/graph.py — LangGraph state machine"]
    entry{"small_talk?<br/>a known intent?"}
    converse["converse<br/>no schema, no tools"]
    dispatch["dispatch<br/>core/intents.py — routed<br/>without asking the model"]
    reason["reason<br/>structured output → thought/action"]
    confirm["confirm<br/>human sign-off; denies by default"]
    act["act<br/>skill.execute(params)"]
    guard["anomaly_guard<br/>deterministic privacy rule"]
    conclude["conclude<br/>out of tool budget; answer anyway"]
    nudge["nudge<br/>action and answer both empty"]
    abort["abort<br/>steps past max_react_steps"]

    entry -->|"just conversation"| converse
    entry -->|"a question about itself"| dispatch
    entry -->|"anything else"| reason
    dispatch -->|"same gates as a chosen action"| act
    reason -->|"action set, under step bound"| act
    reason -->|"action is destructive"| confirm
    confirm -->|"approved"| act
    confirm -->|"denied — becomes an Observation"| reason
    reason -->|"action = none, no answer"| nudge
    reason -->|"step bound reached"| abort
    act -->|"action = scan_environment"| guard
    act -->|"5 tool calls, or 3 on one tool"| conclude
    act -->|"otherwise"| reason
    guard --> reason
    nudge --> reason
  end

  reason -->|"action = none, final_answer set"| tts["pyttsx3"] --> spk(["speaker"])
  converse --> tts
  conclude --> tts
  abort -.->|"spoken apology, no traceback"| tts

  kill(["Delete key"]) -.->|"interrupt flag,<br/>checked each iteration"| graph
```

Everything in that diagram runs on the machine it is installed on. No audio,
frame, or transcript is sent anywhere.

### The reasoning loop

**What LangGraph is doing here.** The loop it replaced was a hand-rolled ReAct
cycle: call the model, regex an `Action:` line out of the reply, run the tool,
paste the result back into a string, call the model again. That works until it
does not, and the ways it fails are all silent — a reworded reply drops the
tool call, a chain has no bound because the counter lives in the wrong
function, and nothing about it can be tested without a model.

LangGraph supplies four things that were missing:

- **An explicit graph.** Nodes are named (`reason`, `confirm`, `act`,
  `anomaly_guard`, `nudge`, `abort`) and the edges between them are ordinary
  Python predicates over the state. "Destructive actions need sign-off first"
  becomes a conditional edge into `confirm`, not a rule the prompt asks the
  model to respect. Every safety property in this project is an edge or a node,
  and that is the whole reason for the migration.
- **One typed state object.** `core/state.py:AgentState` is what flows between
  nodes — the transcript, the chosen action, the step count, the latched
  anomaly flag. There is one place where loop state lives, so `steps` can
  actually bound the chain.
- **A stream of updates.** `graph.stream(stream_mode="updates")` yields each
  node's contribution as it finishes, which is what the WebSocket forwards to
  the HUD. Watching the assistant think is a property of the graph, not
  something bolted on afterwards.
- **Testability without a model.** Because nodes are functions over state, the
  routing tests build the graph against fake skills and a mocked model call and
  assert which node ran. Roughly half this project's tests are only possible
  because of that.

What LangGraph is *not* doing: it holds no memory, does no retrieval, and
makes no decisions. The model chooses; the graph decides what is allowed to
happen next.

`core/registry.py:build_action_schema` derives a JSON Schema from every loaded skill's manifest — `action` is an enum of the loaded skill names plus the sentinel `"none"`, and `thought`, `action`, `action_input`, and `final_answer` are all required. `core/nodes/reason.py` sends that schema to Ollama as the `format` parameter and gets back structured JSON instead of free text, so `core/graph.py`'s `route_after_reason` reads `decision["action"]` directly rather than pattern-matching an `Action:` line. `core/nodes/act.py` executes the chosen skill and the result becomes the next `Observation` in the transcript; the graph loops back to `reason` (through `anomaly_guard` first, if the tool was `scan_environment`) until the model sets `action` to `"none"` and gives its answer in `final_answer`.

Three details that took live runs against the model to get right — see [docs/DESIGN.md](docs/DESIGN.md) for the full account:

- **`action` has to be required, not optional.** Leaving it out of the schema's `required` list let the model return a thought and no action at all — it narrated a plan and selected nothing. Requiring every field, with `"none"` as an explicit enum value, is what forces a commitment.
- **A pending action can arrive with a placeholder answer.** Because `final_answer` is also required, the model sometimes fills it even while calling a tool — observed directly as `"I can see the following: [list of environment details]"` returned alongside a correct `scan_environment` call. Routing checks `action` before `final_answer`, and `reason_node` discards the answer unless the model declined to act.
- **Runaway loops are bounded for the first time.** `steps` increments once per pass through `reason`, and past `max_react_steps` (12) the graph routes to `abort` and returns a plain spoken sentence instead of continuing or crashing. The prompt also has to name the `"none"` sentinel explicitly — without it, five consecutive live runs picked a tool on every turn and ran to the bound regardless of what was asked.

`core/llm_client.py` is the single point of contact with Ollama; every skill and node that talks to the model goes through it, and `llm.host` in settings is what would point inference at another machine.

### Skill discovery

`core/registry.py:discover_skills` walks `skills/` with `rglob("*.py")`, imports each module, and calls its `setup()`. Whatever comes back is registered under its `manifest["name"]`. Adding a capability means dropping a file into `skills/` — no registry to edit, no imports to wire up.

The registry is keyed by manifest name, so **two skills declaring the same name silently overwrite each other**. Keep names unique — `tools/check_manifests.py` enforces it, and CI runs it on every push:

```bash
cd FRIDAY_CORE && python tools/check_manifests.py
```

It reads the manifests with `ast` rather than importing them, so it needs none of the runtime dependencies and works on a machine with no model, no microphone and no camera.

### Interrupts

`core/interrupt_handler.py` runs a global `pynput` keyboard listener. Pressing **Delete** mid-thought sets a flag the graph driver checks on every streamed update, so a reasoning chain that has gone off the rails can be killed without terminating the process.

### The local server

`python -m server.app` starts a FastAPI app on `127.0.0.1:8756`. It exists so a
desktop UI can drive the same assistant the console does; the binding is
checked at startup and refused if it is not a loopback address.

`GET /health` returns the loaded skill names and touches no model, so a client
can poll it cheaply before rendering. `WS /ws` accepts
`{"type": "prompt", "text": "..."}` and streams typed envelopes back —
`thought`, `action`, `observation`, `anomaly`, `answer`, `error` — broadcast to
every connected client. It also accepts `{"type": "cancel"}`, which stops the
turn currently in flight; the flag it sets is reset at the start of the next
turn, so a cancel with no turn running does not affect the next prompt.

Both the console and the server drive one turn runner, `core/session.py`. The
graph-streaming loop is written once and each caller supplies an `emit`
callback: the console's speaks, the server's queues envelopes and speaks. A
turn runs in a worker thread, because `graph.stream` is a synchronous generator
and the model call blocks; events are marshalled back to the event loop.

One turn runs at a time. A second prompt arriving mid-turn is rejected with an
`error` rather than queued — the text-to-speech engine is not re-entrant and
the interrupt flag is global, so two concurrent turns would corrupt both.

Speech can be turned off with `server.speak: false` while developing against
the socket.

A binary frame on the same socket is a recorded utterance — see
[Talking to it](#talking-to-it) below. It is the only thing that ever sends
binary, so the frame needs no envelope of its own.

### Talking to it

Two ways in, one recogniser.

The console entry point opens the microphone itself and waits for the wake
word. The desktop window records with the browser's `MediaRecorder` when
**Ctrl+Shift+Space** is pressed, and sends the WebM/Opus blob down the
WebSocket it already has open; `core/transcriber.py` decodes and transcribes it
with the same faster-whisper model the console uses. Chromium's own
`SpeechRecognition` API is deliberately not used — it uploads audio to Google,
which would quietly end the local-first claim.

**What comes back is run.** Speaking is asking; there is nothing to press
afterwards. Until August 2026 the text landed in the prompt box and waited for a
click, which was a real review step — a mishearing could not become a request
until a human had read it — and it also meant talking to this assistant was
slower than typing to it.

What stands in its place: every destructive skill still stops at the
confirmation gate, so the worst a mishearing reaches unaided is read-only;
**Stop** is live for the length of a turn; and what it heard is printed in the
transcript directly above what it did about it.

#### Or leave it listening: the wake word

The one **Voice** control on the command bar holds the microphone open and acts
when you address it by name — *"Friday, what's the weather"*. It is off on a
fresh install, because a microphone that stays open is not something to inherit
from a default; once you turn it on, it is remembered across restarts.

There used to be two buttons here, **Speak** and **Wake word**. One control
replaced them because two made the operator choose which kind of microphone they
wanted before they had said anything, which is a question about this program's
internals dressed up as a question about their intent. Push-to-talk kept the
hotkey and lost its button.

**Reply to an answer and it hears you without the name.** For eight seconds after
it finishes speaking, the next thing you say is treated as part of the same
conversation — *"what's the weather"* / *"warm and dry"* / *"and tomorrow?"*.

That window is the one place in the voice work where the obvious implementation
is a runaway loop: it answers, the window opens, the microphone records the tail
of its own speech, no name is required, a turn runs, and it answers again. The
platform voice plays out of process, so the HUD's echo cancellation never sees it.

So the window is gated on time, not on content, and on two specific times.
It runs from when **playback finished** rather than when the turn ended, because
the answer goes to a queue and the turn returns while the speakers are still
going. And it is measured against when the audio was **recorded**, not when it
arrived: transcription takes a second or two, so an echo judged on arrival has
already aged past any grace interval you would pick. The delay before the window
opens has to exceed the segmenter's silence window, or the tail of every answer
lands inside it — a test asserts that against the TypeScript constant, since the
two numbers are in different files in different languages.

Four things make it work rather than merely function:

**The recorder runs continuously and is cut during silence**, not started when
speech is detected. Detection takes about 150 milliseconds, and starting a
recorder then costs the first 150 milliseconds of the first word — which is the
wake word. `riday, what's the weather` would look like a broken feature when the
microphone was fine.

**The threshold adapts to the room.** A fixed one is deaf in a quiet room and
triggers on a fan in a noisy one, so the noise floor is tracked while nobody is
speaking and the trigger sits a fixed multiple above it. The floor only moves
during silence, so a long sentence cannot raise it above your own voice.

**The name is matched fuzzily, but only at the start.** `small.en` renders
"Friday" as "Fry day", "Freeday" or "friyay" often enough to matter on an
accented voice, so those count — while *"the deadline moved to Friday"* does
not, because a name used inside a sentence is not an address. The two errors are
not equally expensive: a missed wake word costs one repetition, and a false
trigger runs a turn on a conversation with somebody else in the room.

Anything not addressed to it is **discarded** — not run, not shown, not stored.
A continuous microphone that echoed the room into the HUD would be a transcript
of the room.

Say **"stop"**, **"be quiet"** or **"never mind"** and it stops talking and ends
the turn without starting a new one. That is matched against the whole sentence
and never as a prefix, so *"stop the build"* is still a request — and, usefully,
so is its own *"I have stopped the service"* if it hears itself say it.

#### How it sounds

The answer is rewritten before it is spoken. The model writes markdown whether or
not it was asked to, and a voice pronounces it: `**Ready.**` used to come out as
"star star ready star star", and a URL as half a minute of "h t t p colon slash
slash". `core/speech_text.py` takes the markers off and keeps the words, collapses
a link to "a link", and describes a fenced code block instead of reading twenty
lines of Python aloud. The HUD still shows the answer as written.

It is also queued one sentence at a time rather than one answer at a time, which
is what makes interrupting it work at all: the speech engine is COM and
thread-affine, so `stop()` cannot be called from anywhere but its own thread, and
emptying a queue is the only lever available. The cost of an interruption is the
sentence already in flight.

Say **"speak more slowly"** or **"you're too fast"** and it changes rate on the
next sentence — `core/speaker.py` re-reads the setting before every utterance, so
the confirmation is spoken at the new speed rather than merely claimed.

In this mode the review step above is replaced by the wake word itself, which is
a deliberate trade: hands-free is the whole point, and saying the assistant's
name is an explicit act of address where a recorded blob is merely audio. The
confirmation gate is untouched, so the worst an unreviewed mishearing reaches on
its own is a read-only skill. Rename it with `assistant.wake_word` in settings.

**The model was chosen by measurement.** On this machine — CPU, `int8`, weights
already downloaded, an 8.58-second utterance:

| model | load | transcribe | × realtime |
|---|---|---|---|
| `base.en` | 0.8s | 0.77s | 0.09 |
| **`small.en`** (default) | 1.3s | 2.08s | **0.24** |
| `medium.en` | 3.3s | 8.39s | 0.98 |
| `large-v3-turbo` | ~30s | 11.75s | 1.37 |
| `distil-large-v3` | ~30s | 12.06s | 1.41 |

Push-to-talk means you wait for that before anything happens at all, so
anything approaching realtime is unusable however accurate it is — which rules
out all three large models on CPU here. `small.en` is a quarter of realtime and
clearly better than `base.en` on accented speech, which is the case that
actually matters.

Those are latency numbers on synthesised speech. They settle the model choice
and say nothing about accuracy on *your* voice, so the benchmark ships:

```bash
cd FRIDAY_CORE && python benchmarks/stt_models.py --record
```

It records you reading one sentence and prints every candidate's transcript
side by side. Three settings follow from what you see: `audio.stt_model`,
`audio.stt_vocabulary` (proper nouns to bias toward — the assistant's name,
yours and your location are added automatically), and `audio.stt_download_root`
if the drive holding your home directory is tight.

The model is loaded once, at startup, on a worker thread. Loading it per
utterance would make the button feel broken regardless of which one was picked,
and `/health` must stay instant because the window will not appear until it
answers.

### Knowing how it works

Ask FRIDAY how it works and it answers from
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), not from memory.
`skills/utility/explain_architecture.py` reads that file at runtime, resolves
the topic to a `##` section, and returns it verbatim. The skill is marked
`terminal`, so the section is the answer and the turn ends there.

This is not politeness about sourcing. Asked what LangGraph is used for, the
model's own reasoning step offered *"a graph-based natural language processing
library that I use to represent and reason about complex relationships between
concepts"* — fluent, confident, and wrong in every particular. The skill
returned the real answer and, being terminal, the guess never reached the
operator as an answer.

The same run showed a second problem worth stating: the `thought` field is
spoken aloud *before* the tool runs, so a thought that previews an answer
broadcasts a guess as though it were fact. The system prompt now requires the
thought to state the plan and nothing else. Re-run afterwards, it said *"I will
call explain_architecture with topic 'LangGraph'"* — and the turn dropped from
16.3s to 3.8s, because there was no essay to generate first.

Adding a `##` section to `docs/ARCHITECTURE.md` adds a topic with no code
change. A test asserts every topic advertised in the manifest exists as a
heading, so renaming one fails a test rather than silently answering the wrong
question.

### Continuous screen awareness

Off by default. With `screen.enabled: true`, a background thread grabs the
monitor, decides whether enough has changed to be worth looking at, and asks a
local vision model to describe it. The latest description is folded into the
next turn's prompt once, and streamed to the HUD as a `screen_context` event.

```bash
ollama pull moondream
```

Three things shape the design, all measured on this machine rather than
assumed:

- **A describe pass is cheap: ~0.19 s median at 1280 px, and a screen grab is
  28 ms.** The interval is 5 seconds, which is comfortable rather than tight.
- **Two models on one Ollama process contend.** The same call took **4.86 s**
  while the language model was working. So the watcher is told when a turn is
  in flight and **skips that cycle** rather than queueing it, since a queued
  call would land mid-turn and pay the same penalty anyway.
- **A change gate makes an idle desktop nearly free.** Frames are compared by
  perceptual hash, and a screen that has not changed costs a grab and a hash,
  no inference.

`vision/describers/` puts the model behind a `Describer` protocol.
`OllamaVLM` is the working one; an OpenVINO implementation drops in beside it
without the graph or the server changing.

**The descriptions are rough.** moondream is a small model, and it will
misread a screen — at 768 px it invented a program name that was not there,
which is why frames are sent at 1280. Treat it as ambient context, not fact.
Empty replies are dropped rather than published.

### Pointing inference at another machine

`llm.host` and `vlm.host` are independent settings — the language model and
the vision model do not have to live on the same box, or even the same kind
of hardware. The natural split is the small vision model on a Raspberry Pi 5
and the language model staying local, since a describe pass is cheap and
tolerates the extra network hop far better than the reasoning loop would; the
reverse works too if the heavier model is what needs to move.

```yaml
llm:
  host: http://127.0.0.1:11434   # keep local

vlm:
  host: http://192.168.1.42:11434   # Pi 5 on the LAN
```

On the remote machine, Ollama has to be told to listen beyond localhost:

```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

**Every call in the project still goes through `core/llm_client.py`,** so this
is the only thing that changes — no other file cares where inference runs.

**Unreachable hosts fall back, they do not fail every call.** The first time
a configured host is used, `core/llm_client.py:_resolved_host` probes it
(`GET /api/version`, a ~1.5 s timeout) and, if it does not answer, falls back
to `http://127.0.0.1:11434` and prints exactly that:

```
[!] http://192.168.1.42:11434 is unreachable; falling back to http://127.0.0.1:11434 for the rest of this run.
```

The probe runs once per configured host per process, not once per call —
resolving again means restarting. `llm.host` and `vlm.host` are resolved
independently, so one can be offloaded successfully while the other falls
back.

**Nothing here is authenticated or encrypted.** This is a LAN-only
arrangement: `OLLAMA_HOST=0.0.0.0` exposes the model — and the ability to run
inference on that machine — to anything else on the same network, with no
password and no TLS. Do this only on a network you trust, and see Known
limitations below.

### The desktop shell

`desktop/` is an Electron + React + Vite front end for the same server — an
ordinary, resizable desktop window with a custom title bar carrying minimise,
maximise, close and a **pin** button for always-on-top.

**It was a transparent, always-on-top, click-through overlay, and that failed
on a real machine.** The window began click-through and only became
interactive on a `mouseenter` in the renderer — but Electron hands mouse
events over a `-webkit-app-region: drag` surface to the OS rather than to the
page, so a pointer crossing the title bar made those enter/leave events
unreliable and the flag latched. Latched on, nothing could be dragged or
clicked. Latched off, an always-on-top window silently swallowed every click
inside its rectangle and the desktop underneath became unusable. There were
also no window controls at all, because a frameless overlay was never meant to
be minimised. Opaque and ordinary is what fixed all three; always-on-top is
now a button rather than the default.

The layout is three columns, all visible at once rather than behind tabs:

| Region | Shows |
|---|---|
| **Left rail** | The reactor — idle, reasoning or fault — plus the socket address, skill count, turns completed and events received. |
| **Centre** | The event stream, one line per event, colour-coded by type. The step-by-step types (`thought`, `action`, `observation`, `status`) can be muted with the chips above it; `answer`, `error` and `anomaly` deliberately cannot, so a filter can never hide the end of a turn. |
| **Right rail** | The skills the backend actually loaded, read from `GET /health` on a fifteen-second poll, and the screen watcher's latest ambient description when screen awareness is on. |

A pending destructive action takes over the panel area entirely, listing the
proposed call one parameter per line — the one moment the HUD asks a human to
authorise something irreversible is not a good place for a single-line JSON
blob nobody reads before clicking.

Everything on screen is either observed in this window or came off the wire.
There is no latency graph, no CPU gauge and no token counter, because the
backend publishes none of those and a readout of invented numbers is worse
than one with fewer numbers.

**`/health` is fetched by the main process, not the renderer.** A renderer
fetch to `127.0.0.1:8756` is cross-origin, and the only way to permit it would
be CORS headers on a server that has no authentication — which would let any
web page the operator visits read the endpoint. Main runs in Node, where
same-origin policy does not apply, so the call goes over the preload bridge
and the server's surface stays as narrow as it was.

The prompt box keeps a shell-style history on the up and down arrows, and its
button becomes **Stop** only while a turn is actually in flight — cancelling
between turns does nothing, because the backend resets the interrupt flag at
the start of each one.

**Speak** records a request; **Ctrl+Shift+Space** does the same from anywhere,
since needing to find and focus the window first defeats the point. The
microphone is acquired per recording and released on stop rather than held open
for the life of the window — that costs a couple of hundred milliseconds, shown
honestly as an *Opening* state so nobody starts talking into a microphone that
is not live yet, and it keeps the operating system's "in use" indicator
truthful. Recording is a toggle rather than a hold, because a global shortcut
fires on press and has no release event, and one mental model is better than
two; a forgotten toggle stops itself after 45 seconds. Electron's permission
handler is set explicitly to allow audio capture and refuse everything else —
without one, Electron grants every permission a page asks for, camera
included.

**It attaches rather than assumes ownership.** On launch it probes `/health`
first; if something already answers on 8756 it attaches and will not stop that
server on quit. Only a server it spawned itself is ever killed. The kill uses
`taskkill /T` on Windows, and that is load-bearing rather than belt-and-braces:
`friday_env\Scripts\python.exe` is a redirector that launches the real
interpreter as a child process, so signalling only the process it spawned would
leave the actual server running.

```bash
cd desktop
npm install
npm run dev      # or: npm run build && npx electron .
```

If `node_modules/electron/dist/` is missing after install, the Electron binary
download did not run — `node node_modules/electron/install.js` fetches it.

**Building an executable:**

```bash
cd desktop
npm run package
```

That writes `desktop/release/`: a portable `FRIDAY <version>.exe` that runs
with no installation, and `FRIDAY Setup <version>.exe`, an installer. Both are
about 86 MB, and both are unsigned — Windows SmartScreen will warn on first
run.

**The installer ships the shell only.** The Python backend, its virtualenv,
the vision model and a 4.9 GB language model are not bundled; an installer
carrying all of that would be several gigabytes and stale the moment a skill
changed. A packaged app finds the backend through the `FRIDAY_CORE_DIR`
environment variable, or in a `FRIDAY_CORE` directory beside its own
executable:

```powershell
$env:FRIDAY_CORE_DIR = "E:\FRIDAY\FRIDAY_CORE"
& ".\release\FRIDAY 0.1.0.exe"
```

The window stays hidden until the backend answers or a 60-second budget
expires, because a cold start loads the vision model and every skill and takes
around twelve seconds.

---

### Speaking without being spoken to

Off by default, under `proactive` in settings. Two things it will do
unprompted: deliver reminders you set, and read a short briefing at a
configured time.

**The briefing does not go through the reasoning loop, and that is the whole
design.** A free agent turn picks its own tools, and this project has watched
that go wrong twice in one day — a capabilities question that drafted two
documents and took a webcam photo, and a news request answered with invented
headlines. Supervised those are irritating; unattended at 08:00 they are not
acceptable. So `core/briefing.py` calls a fixed, read-only list of skills
directly and spends exactly one model call turning the results into prose. It
cannot choose a different tool and it cannot write anything, because it is
never asked what to do.

Three behaviours worth knowing:

- **A proactive message waits while a turn is running.** It is not a turn
  itself: it never sets the single-flight flag, so it cannot make your next
  prompt bounce, and it never enters the conversation transcript, so the model
  never mistakes a briefing for something you said.
- **Quiet hours silence the voice, not the message.** A briefing during quiet
  hours still appears in the HUD; it just does not speak.
- **A missed reminder fires late, a missed briefing is dropped.** These differ
  on purpose. A reminder is a promise you stopped holding yourself, so late is
  recoverable and silence is not — it says how late it was. A breakfast
  briefing delivered at lunchtime is stale noise.

### OS automation and the confirmation gate

Some skills type keystrokes and delete files. Those run behind two independent
layers, and it is worth being clear that they do different jobs.

**The confirmation gate** is a node in the graph. A skill whose manifest sets
`"destructive": True` does not route from `reason` straight to `act` — it
routes through `confirm` first, which asks a human and only lets the call
through on a yes. The console driver asks on stdin; the server sends a
`confirmation_required` event carrying the real proposed `{name, input}` and
blocks the turn until the HUD answers or sixty seconds pass.

**It defaults to deny.** A caller that wires no confirmation mechanism at all
gets a refusal on every destructive action rather than an open door. That is
the whole design: a gate whose safety depends on every future integration
point remembering to configure it correctly is not a gate. A denial is not an
abort either — it becomes the next `Observation`, so the model is told plainly
that it was refused and can respond to that, and a model that simply keeps
re-proposing the same action is stopped by the ordinary step bound.

**The path allowlist** is the layer beneath it. `manage_files` refuses any path
that does not resolve inside `filesystem.allowed_roots` — `~/FridayWorkspace`
by default, deliberately not the whole home directory, which holds Documents,
Downloads and `.ssh`. Resolution happens *before* the containment check, so
`..` segments and symlinks are checked by where they actually land rather than
by how they are spelled, and a path outside every root is **refused, not
clamped** into the nearest one. Confirmation stops a bad request from being
rubber-stamped; the allowlist makes a whole class of request unrepresentable
regardless of what anyone approves.

`manage_files` marks its **entire** manifest destructive, including the
read-only `list` and `read` actions, so reading a file prompts for
confirmation too. This is a known consequence of the manifest contract rather
than an oversight: `destructive` is one flag per skill, and `action` is just a
model-supplied parameter that routing never sees. Splitting the skill in four
to get finer-grained gating was considered and rejected as more manifest
surface than the convenience is worth.

### The camera privacy rule

After every `scan_environment`, `core/nodes/anomaly_guard.py` checks two
conditions: more than one person in frame, or the workstation missing from the
detections. Either one is an anomaly, and the state latches until a scan reports
exactly one person with the laptop back — hysteresis, so a single bad frame does
not flip it back and forth. The check is plain Python that runs every time, not
a sentence in the system prompt.

What it *does* about an anomaly is configuration, and the useful default is the
quiet one:

```yaml
privacy:
  auto_mute: false      # mute system audio on an anomaly
  announce_only: true   # read only when auto_mute is false
```

| `auto_mute` | `announce_only` | Behaviour |
|---|---|---|
| `false` | `true` | Says what it saw, changes nothing — **the default** |
| `true` | ignored | Mutes system audio and says so |
| `false` | `false` | The guard is silent entirely |

It muted unconditionally in earlier versions. That was a defensible rule and the
wrong thing to inherit without asking: an assistant reaching into the machine's
audio because a webcam frame looked crowded is an intervention, and
interventions should be chosen. The detection is unchanged — it was never the
part that was wrong.

When muting is enabled it goes through the CoreAudio interface, which can set a
*target* state and confirm it afterwards with `GetMute()`. The media-key
fallback beneath it only toggles, so it is used only when the current state can
be read and is wrong; a state that cannot be read is reported as a failure
rather than guessed at. The narration reports what the call actually returned,
so FRIDAY does not claim to have muted anything it did not mute.

The mute is not persisted anywhere. Turning `auto_mute` off on a machine the old
behaviour has already muted leaves it muted — ask for an unmute once.

---

## Writing a skill

A skill is a class with a `manifest`, an `execute()` method, and a module-level `setup()` factory:

```python
class WeatherSkill:
    def __init__(self):
        self.manifest = {
            "name": "check_weather",          # unique; the LLM calls this
            "description": "Fetches the current forecast for a named city. "
                           "Use this only for weather questions.",
            "parameters": ["city"],
            # Optional, defaults to False. Set it True and every call to this
            # skill goes through the confirmation gate first — see above.
            "destructive": False,
        }

    def execute(self, params=None):
        city = (params or {}).get("city")
        if not city:
            return {"status": "error", "message": "I need a city name."}
        return {"status": "success", "message": f"It is 22 degrees in {city}."}


def setup():
    return WeatherSkill()
```

`execute()` must return a dict with `status` and `message`. The `message` is what the model receives as its `Observation`, so it should read as a sentence, not a data structure.

**The `description` field is the routing logic.** It is the only information the model has when deciding whether to call your skill, and vague descriptions cause misrouting — which is why several of the shipped manifests contain explicit negative instructions ("DO NOT use this to open applications"). Treat it as prompt engineering, not documentation.

---

## Included skills

Forty-eight on disk, forty-seven loaded — `track_price` ships disabled, see below.
**`destructive`** marks a skill that routes through the confirmation gate;
**`terminal`** marks one whose output is the whole answer, ending the turn.

### Reading what is on the machine

| Skill | What it does |
|---|---|
| `read_document` | Text out of a PDF, DOCX, TXT or Markdown file, with DOCX tables walked as well as paragraphs |
| `read_spreadsheet` | CSV and XLSX: shape summary, exact column statistics, or the rows matching a value — arithmetic done in Python, not by the model |
| `search_files` | Finds files in the workspace by name or by content, ripgrep when present |
| `ocr_screen` | Exact character extraction from the screen through the Windows OCR engine — distinct from `describe_screen`, which guesses |
| `screenshot` | Saves a PNG of the screen or a region into the workspace |
| `clipboard` | Reads what was copied, or copies text onto the clipboard |

### The web

| Skill | What it does |
|---|---|
| `web_search` | Looks a fact up across three keyless APIs — DuckDuckGo Instant Answer, MediaWiki, Google News — and has the model extract the answer |
| `read_news` | Current headlines from Google News RSS, general or by topic, for the model to summarise |
| `weather` | Current conditions and a three-day forecast from Open-Meteo, no API key |
| `read_webpage` | Fetches a URL and extracts its readable text, for summarising a link or a story behind a headline |
| `open_url` | Opens a link in the default browser — http and https only, because `file://` and `shell:` read files and launch applications |
| `track_price` | Watches a figure on a page over time. **Disabled by default**: the code works and has not run on a schedule for the weeks that would justify trusting it |

### Vision

| Skill | What it does |
|---|---|
| `scan_environment` | Captures a webcam frame and runs YOLO11 object detection through OpenVINO |
| `describe_screen` | Captures the screen on request and describes it — a fresh look, distinct from the ambient watcher |
| `annotate_image` | Runs detection on an image file and writes a boxed, labelled copy, reusing the exported model |
| `check_camera_stream` | One frame from an RTSP or HTTP camera, reporting which of the three failure modes happened if it is down |

### Development

| Skill | What it does |
|---|---|
| `inspect_repo` | Branch, uncommitted changes, recent commits — read-only git from a fixed subcommand table |
| `search_code` | Matches in a configured source tree as `file:line` |
| `gpu_status` | GPU model, VRAM, utilisation and driver, plus what the OpenVINO runtime can actually target — **terminal** |
| `run_tests` | pytest or npm test in an allowed directory, returning the failure summary rather than the log — **destructive**, confirmed |
| `run_command` | One program from an allowlist, in an allowed directory, with no shell — **destructive**, confirmed |

### Knowing itself

| Skill | What it does |
|---|---|
| `core_identity` | Answers "who are you" from the skills actually loaded, without burning a reasoning step |
| `explain_architecture` | Answers "how do you work" from `docs/ARCHITECTURE.md`, verbatim, rather than from the model's idea of how assistants work — **terminal** |
| `explain_last_turn` | What it actually did last turn: tools called with parameters, what returned, what was refused — **terminal** |
| `skill_health` | Which skills failed to load and why, separating a missing package from missing data — **terminal** |
| `diagnose_self` | The ten things that have actually gone wrong here, checked in one question — **terminal** |
| `manage_settings` | Reads and changes its own settings in conversation, from a key allowlist — **destructive**, confirmed |
| `voice_control` | How it speaks: faster, slower, a rate in words per minute, aloud or not, which voices are installed — **terminal** |

### The machine

| Skill | What it does |
|---|---|
| `system_check` | CPU and RAM utilisation |
| `disk_report` | Free space on every drive, and optionally what is eating a directory — **terminal** |
| `network_status` | Local addresses, whether the internet answers, latency to a host — **terminal** |
| `manage_processes` | Lists processes, or ends one by name — refuses its own tree and critical system processes. **destructive**, confirmed |
| `power_control` | Lock, sleep, restart, shut down — the last two delayed and abortable. **destructive**, confirmed |
| `launch_application` | Opens desktop applications, with per-OS executable name mapping |
| `media_control` | System volume and playback: sets a level exactly through CoreAudio, steps up and down, mutes, pauses, skips (Windows) |
| `window_control` | Lists, focuses, minimises and maximises desktop windows through `user32` |
| `send_keys` | Types text or presses a hotkey combination — **destructive**, confirmed |
| `manage_files` | Lists, reads, moves and deletes inside an allowlisted workspace — **destructive**, confirmed |

### Life admin

| Skill | What it does |
|---|---|
| `reminders` | Sets, lists and cancels reminders that FRIDAY delivers on its own, even with nothing open |
| `task_list` | A lasting to-do list — no times, nothing delivers it, deliberately not the reminder store |
| `journal` | Dated notes, stored verbatim, read back by day or week |
| `calendar` | Today's and this week's events from local `.ics` files, recurrence expanded for the common rules |
| `check_email` | Unread senders and subjects over IMAP. Read-only, headers only, password from the environment |
| `translate` | Translation through the local model, nothing leaving the machine |
| `world_time` | The clock elsewhere, and days between dates — the local date and time is in every prompt already — **terminal** |
| `manage_memory` | Stores and retrieves facts in a local SQLite vault, synthesising a natural reply on retrieval |
| `draft_document` | Generates prose with the local model and saves it as a `.docx` |
| `log_fleet_market_data` | Appends structured rows to a CSV ledger |

### Three permission lists, deliberately separate

The skills above reach the filesystem through three different allowlists, and the
separation is the design rather than an accident of growth:

| Setting | Who uses it | What it grants | Default |
|---|---|---|---|
| `filesystem.allowed_roots` | `manage_files`, `read_document`, `read_spreadsheet`, `search_files`, `screenshot`, `annotate_image`, `disk_report` | A workspace where files may be read *and written and deleted* | `~/FridayWorkspace` |
| `projects.allowed_roots` | `inspect_repo`, `search_code` | Source trees that may only be read | empty — both refuse until set |
| `commands.allowed_roots` + `allowed_executables` | `run_tests`, `run_command` | Directories where something may be **executed**, and which programs | empty — both refuse until set |

Collapsing any two would mean that letting FRIDAY describe a repository also let it
run a test suite there, or that letting it read a project also let it delete files
in it. Different questions, different answers.

### Not built, deliberately

`calculator` — the system prompt forbids inventing a maths tool and requires
arithmetic in the reasoning step, because the model kept hallucinating one; a
calculator skill would fight a rule that exists for a reason. `run_python` —
arbitrary evaluation with no meaningful containment, which is `run_command` without
the boundary that makes it defensible. `face_recognition` on the webcam feed — easy
here and a privacy decision rather than an engineering one, so it needs an explicit
yes rather than a default. Anything that posts publicly — a confabulated headline is
embarrassing in a transcript and permanent on the internet.

---

## Setup

**Prerequisites:** Python 3.10, [Ollama](https://ollama.com) running locally, a microphone, and a webcam for the vision skill.

```bash
git clone https://github.com/GovindRajoria/friday-assistant.git
cd friday-assistant/FRIDAY_CORE

# Installs system dependencies, Ollama, the model, and the Python environment
bash skills.sh
```

Or manually:

```bash
python -m venv friday_env
source friday_env/bin/activate      # Windows: friday_env\Scripts\activate
pip install -r requirements.txt
ollama pull llama3.1
```

**Configure.** Copy the example settings and edit them:

```bash
cp config/settings.example.yaml config/settings.yaml
```

`config/settings.yaml` is git-ignored — it holds your name, microphone index, and camera index, none of which belong in version control. Every value falls back to a default in `core/config.py`, so an empty file still boots.

**Generate the vision model.** The YOLO weights and the exported OpenVINO IR are not committed (they are regenerable, and one is 10 MB):

```bash
python benchmarks/openvino_yolo11_opt.py
```

This downloads `yolo11n.pt`, exports it to OpenVINO IR in `yolo11n_openvino_model/`, and benchmarks the result.

**Run:**

```bash
python -m core.main
```

Say the wake word, or press any key to type a command instead.

---

## Platform support

Developed and tested on Windows 11 with Python 3.10.

The core loop, vision, web search, memory, and document skills are cross-platform. Two areas are not:

- **`media_control`** drives Windows media keys and the CoreAudio COM interface. On Linux and macOS the skill loads but returns a clear error rather than vanishing from the registry.
- **Keyboard-interrupt detection in `core/listener.py`** uses `msvcrt` on Windows and falls back to a `select` poll on stdin elsewhere. The fallback is untested.

---

## Repository layout

```
FRIDAY_CORE/
├── core/
│   ├── main.py              Console entry point: skill discovery, graph construction, speech
│   ├── session.py           run_turn: the graph-streaming loop, shared by console and server
│   ├── graph.py             LangGraph state machine: reason / confirm / act / anomaly_guard / nudge / abort
│   ├── state.py             AgentState TypedDict, the single source of loop truth
│   ├── registry.py          Skill discovery + JSON schema derived from the manifests
│   ├── prompts.py           System prompt and per-turn user message construction
│   ├── llm_client.py        The single Ollama client; every call in the project goes through it
│   ├── nodes/
│   │   ├── reason.py        Structured-output call → thought / action / final_answer
│   │   ├── confirm.py       Human sign-off before a destructive skill runs; defaults to deny
│   │   ├── act.py           Skill dispatch + observation capture
│   │   └── anomaly_guard.py Deterministic privacy rule; muting is opt-in
│   ├── config.py            Settings loader with layered fallbacks
│   ├── paths.py             The workspace allowlist, shared by every disk-touching skill
│   ├── project_roots.py     The read-only project and execute-here allowlists
│   ├── notes_store.py       JSON records behind task_list and journal
│   ├── turn_log.py          In-process record of recent turns, read by explain_last_turn
│   ├── listener.py          faster-whisper STT + wake word + typed-input fallback (console)
│   ├── transcriber.py       The loaded STT model the HUD's microphone goes through
│   ├── speaker.py           pyttsx3 TTS
│   └── interrupt_handler.py Global kill-switch listener
├── server/
│   ├── app.py               FastAPI: /health probe, /ws turn stream, single-flight
│   └── events.py            Typed event envelopes sent over the socket
├── vision/
│   ├── capture.py           mss grab, downscale, and the perceptual-hash change gate
│   ├── watcher.py           Background capture loop; skips a cycle while a turn runs
│   └── describers/          Describer protocol: OllamaVLM now, OpenVINO later
├── skills/                  Auto-discovered capabilities, grouped by domain
│   ├── reading/             read_document, read_spreadsheet, search_files
│   ├── dev/                 inspect_repo, search_code, gpu_status, run_tests, run_command, check_camera_stream
│   ├── vision/              scan_environment, describe_screen, screenshot, ocr_screen, annotate_image
│   ├── os_control/          files, windows, keys, processes, power, media, browser
│   ├── utility/             identity, self-knowledge, settings, memory, tasks, journal, calendar, email
│   ├── web/                 search, news, weather, pages, price watching
│   └── business/            the CSV ledger
├── benchmarks/              YOLO11 / OpenVINO export, and stt_models.py for the STT comparison
├── tools/
│   └── check_manifests.py   Static manifest validation; what CI runs
├── tests/                   pytest — graph routing, the guard, three allowlists, run_command's boundary, the descriptions, the server
├── config/
│   └── settings.example.yaml
├── requirements.txt
└── skills.sh                Cross-platform bootstrap

desktop/
├── electron/
│   ├── main.ts              Attach-or-spawn backend, health gate, window, tree kill
│   └── preload.ts           contextBridge; the renderer's only route to Electron
└── src/
    ├── reducer.ts           Pure event → HUD state; unit-tested without a socket
    ├── hooks/useAgentSocket.ts  Socket, reconnect, dispatch into the reducer
    ├── hooks/useMicrophone.ts   Push-to-talk capture; releases the mic on stop
    ├── events.ts            Event-type union, gated against server/events.py
    └── components/          Reactor, Transcript, PromptInput, MicButton
```

How the whole thing fits together, in one page: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
— which is also the file FRIDAY itself reads when asked. Design decisions and
the reasoning behind them: [docs/DESIGN.md](docs/DESIGN.md).

---

## Known limitations

Stated plainly, because they are the honest state of the project:

- **The skill count went from 19 to 46 in one batch, and per-skill routing accuracy is unmeasured.** This is the largest unverified claim in the project. Every skill has been exercised directly and the descriptions were audited pair by pair for the overlaps that compete — `read_document` against `manage_files`, `ocr_screen` against `describe_screen`, `run_command` against `run_tests` — with `tests/test_skill_routing_surface.py` pinning those disambiguations. None of that is a measurement. Whether the model picks correctly among 45 tools more or less often than among 19 is not known, and the honest answer to "did adding these make it better" is that nobody has scored it. `skills.disabled` in `settings.yaml` exists for exactly this: a group that turns out to confuse routing is a config edit, not a revert.
- **Several new skills are unverified against the thing they talk to.** `check_email` has never run against a live IMAP server — there is no mail account on the development machine, so its parsing and every error path are tested and the conversation with a real server is not. `calendar` is tested against `.ics` files this project generated, not against a real export from Outlook or Google. `track_price` ships disabled for the same class of reason: it works when called, and it has not run on a schedule over the weeks that would justify trusting it to.
- **The ripgrep path in `search_files` and `search_code` is unexercised here.** `shutil.which("rg")` returns None on this machine, so the bounded Python scan is what actually runs. The ripgrep branch is written and unit-tested against its exit codes, and has never spoken to a real ripgrep.
- **`calendar` implements a subset of recurrence, not RFC 5545.** `FREQ` of DAILY, WEEKLY, MONTHLY or YEARLY with `INTERVAL`, `UNTIL` and `COUNT`. Monthly and yearly steps are approximated as 30 and 365 days, so a "first Monday of the month" rule or a long monthly series will drift. Anything it cannot expand is reported as possibly missing rather than dropped silently, which makes the gap visible instead of mysterious.
- **`ocr_screen` needs a Windows OCR language pack** and says so when one is absent. Verified on this machine — 147 lines and 2,479 characters read off a real screen — and Windows-only.
- The anomaly guard is coupled to one skill by name. `route_after_act` in `core/graph.py` only routes to `anomaly_guard` when `action == "scan_environment"`; a second skill producing detections worth guarding on would need that check extended by hand.
- `media_control`'s media-key fallback is only exercised by unit tests with the COM layer stubbed, never on hardware, because the machine it was written on does not need it. Volume and mute both go through CoreAudio now, where a target state can be set and then read back to confirm it; the keys are used only for a relative step, where there is no target state to get wrong, and for playback, where a toggle is what was asked for. Setting a level used to be 50 `volumedown` keypresses and a step back up on an assumed 2% increment — that is gone.
- **Speech recognition is a latency compromise and the accuracy half is unmeasured on real voices.** `small.en` on CPU at `int8` was chosen because every larger model measured at or past realtime on this machine, which push-to-talk cannot absorb. The comparison that picked it used synthesised speech, so it establishes the timings and nothing about word error on an accented voice in a room — run `benchmarks/stt_models.py --record` to settle that for yourself, and raise `audio.stt_model` if you would rather wait.
- **The wake word's audio half has never been tested against a real voice in a real room.** The decision half is: `core/wake_word.py` is covered from both directions, including the mishearings and the "moved to Friday" false trigger, and the socket gate has tests for acting, ignoring and staying silent. What has *not* been measured is whether the energy threshold and the 850 ms silence cut behave on this operator's voice, microphone and room — whether it cuts people off mid-sentence, misses a quiet question, or wakes on a television. Those constants (`SPEECH_MULTIPLIER`, `SILENCE_MS_TO_END` and `MIN_SPEECH_MS` in `desktop/src/hooks/useAlwaysListening.ts`, and the follow-up window's eight seconds and 1.2s grace in `server/app.py`) were reasoned about and not tuned — `MIN_SPEECH_MS` moved from 250 to 150 on an argument about how long the word "stop" takes, not on a measurement — and tuning them needs a person talking, not a test.
- **Continuous listening transcribes every utterance in the room, locally.** Nothing leaves the machine and unaddressed speech is discarded rather than stored — but it *is* transcribed before it can be discarded, which costs roughly a quarter of one CPU core while anyone is talking, and means the model sees speech that was not meant for it. A dedicated wake-word model would only process audio after a trigger; that was considered and rejected for this build because openWakeWord ships no pretrained "friday" and training one is its own project.
- **The voice path has never been verified end to end by a person speaking to it.** The two halves have been checked separately, in the packaged build, through DevTools against the live DOM — a Chromium window on this platform does not screenshot faithfully, so an image of it is not evidence. The server half: a real WebM/Opus blob sent over the socket came back correctly transcribed in 3.6s. The renderer half, re-observed on 2026-08-07 after the control was rewritten: the packaged app shows exactly one voice control and neither of the two it replaced; clicking it really does open the microphone and move to `Listening`; the choice is written to `localStorage`; and after a relaunch it comes back up listening from that preference alone. What is still unmeasured is the join: a spoken sentence going in one end and the right words coming out the other. None of the renderer audio code (`getUserMedia`, `MediaRecorder`, the permission prompt, the hotkey) has automated coverage, and neither the follow-up window nor the stop words have been heard working — their tests drive a fake speaker and a synthetic clock. Talking to it is the only check that counts.
- **Web lookup depends on third-party endpoints that can close without warning.** `web_search` originally scraped DuckDuckGo's HTML; on 2026-08-03 both the lite and html endpoints began answering 202 with zero results, and every web question failed as "I couldn't find any results" — indistinguishable from an empty search. It now uses documented APIs and falls through three sources rather than one, which makes a single outage survivable, not impossible. `read_news` is localised to India/English in `LOCALE`; change those two values for another region.
- **The model will claim to have done things it has not done.** Caught live: asked to set a reminder, it replied "Reminder set" without calling the skill, and nothing was stored — the worst available failure for a feature whose value is that you stop holding the thing in your head. The system prompt now states that "done", "saved" and "reminder set" are true only when a tool just said so in an Observation. That is an instruction, not a guarantee.
- **The model will still answer a current-events question from memory if allowed to.** Caught live: asked to summarise today's news, it made no tool call and invented plausible headlines. The system prompt now carries an explicit rule that anything time-sensitive must come from a tool result, and the graph refuses to re-run a tool call identical to the one it just made — but both are instructions and a guard, not a guarantee that a local model never confabulates.
- **Nothing that needs a model, a microphone or a camera is tested.** CI lints, compiles, validates every skill manifest, and runs five hundred and twenty-three pytest cases against the graph, the guard and its privacy switch, the confirmation gate, all three path allowlists, `run_command`'s boundary, the transcription path, the mute path with the COM layer stubbed, the skill descriptions, and the server — five hundred and twenty-two on a machine that cannot create symlinks, where one allowlist case skips, which is what the development machine does — real gates, but all of them run against fake skills and a mocked model client. The reasoning loop against a real model, speech recognition, synthesis, and every skill's `execute()` are exercised only by hand.
- **The confirmation gate stops execution, not proposals.** A local model can still decide to delete something it should not; what the gate guarantees is that a human sees the actual call and says yes before it runs. It is a backstop for judgment, not a content filter, and a human who approves without reading has bypassed it entirely.
- The gate's granularity is one flag per skill, not per action. `manage_files` is wholly destructive, so listing a directory or reading a file prompts for confirmation exactly like deleting one does. Correct, but noisier than it needs to be.
- `send_keys` types into whatever currently has keyboard focus, which is not necessarily what the operator believes has focus. It presses keys; it does not know what is listening. Nothing verifies the target window before the keystrokes go out.
- `window_control` is Windows-only. It calls `user32` through `ctypes` and reports that it is unsupported elsewhere rather than failing at import.
- The path allowlist protects `manage_files` and nothing else. Other skills that write to disk — `draft_document`, the memory vault — predate it and are not routed through it.
- The server has no authentication. It is safe only because it refuses to bind to anything but a loopback address — any process on the machine can drive it, and it can launch applications and write files. Exposing the port would hand those capabilities to the network.
- **Offloaded inference (`llm.host` / `vlm.host` pointed at another machine) is unauthenticated and unencrypted.** `OLLAMA_HOST=0.0.0.0 ollama serve` on the remote box puts its model, and the compute to run it, on the LAN with no password and no TLS — anyone on that network can talk to it exactly as this project does. This is a same-network, trusted-LAN feature, not something to expose past a router. The reachability probe only decides where a request goes; it does not add any security to the connection.
- A remote vlm.host going quiet is handled — the watcher backs off, reports the failure, and resumes on its own when the host comes back — but a remote llm.host going quiet mid-turn still surfaces as a failed turn (an `error` event), not a silent retry. The fallback in `core/llm_client.py` only applies at first resolution per process; it does not re-probe a host that was reachable at startup and later drops.
- If the Electron main process is killed outright rather than closed, a backend it spawned is orphaned. The kill runs from `before-quit`, `window-all-closed` and `process.on('exit')`, none of which fire on a hard kill. There is no watchdog; the next launch attaches to the survivor rather than colliding with it.
- The orb never shows "speaking". The backend hands narration to a fire-and-forget speech thread and emits nothing when it finishes, so there is no event that could return the orb to idle afterwards — it goes straight back to idle when a turn ends rather than claiming a state whose end cannot be observed.
- Screen descriptions come from a small model and are frequently wrong in detail. They are ambient context injected into the prompt, not a source of truth, and nothing downstream validates them.
- Screen capture is local and never leaves the machine, but it is still a capture of whatever is on screen — it is off by default and worth leaving off while handling anything sensitive.
- The packaged executable is unsigned, and it contains only the shell. It cannot run without a Python backend already installed on the machine, located through FRIDAY_CORE_DIR or placed beside the executable.
- The HUD is unit-tested at the reducer only. CI typechecks and builds the shell and runs the reducer tests, but nothing exercises Electron itself — the window, the health gate and the process kill are verified by hand.
- **The first answer to a confirmation wins, from any connected client.** The pending confirmation is one module-level object on a single-flight server, and its wait returns the moment anything resolves it; a second answer arriving afterwards sets a flag nothing is waiting on and is discarded. So an approval cannot be taken back by a denial sent a moment later, and a HUD left open on a shared screen is a client that can answer for you. Combined with the point below, one stray click both approves the action and makes any subsequent denial a no-op.
- The confirmation prompt is a normal button. Nothing distinguishes a considered approval from an accidental click, and during development a stray click on it approved three deletions that the driving script had not asked for. The sixty-second timeout is the only thing standing between an unattended HUD and a request that waits forever — it denies, but it denies quietly.
- `test_suite.txt` drives an end-to-end batch runner (`run the test suite`) that logs model output for manual review — useful for regression-spotting, not a substitute for unit tests.
- Structured output narrows how a hallucinated tool call can happen, but it does not eliminate model error generally — `action_input` is an open `object` with no per-skill parameter schema, so a wrong or missing argument inside a valid action is still possible and is not validated before `act_node` calls `skill.execute()`.

---

## Licence

**GNU AGPL-3.0** — see [LICENSE](LICENSE).

This project uses [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) for object detection, which is licensed under AGPL-3.0. Because FRIDAY loads Ultralytics at runtime, the combined work inherits AGPL-3.0 terms; the licence choice here follows from that dependency. If you need permissive licensing, substitute the detector in `skills/vision/scan_environment.py`.

## Author

**Govind Kumar** — AI/ML Developer, Metro Infrasys Private Limited
[GitHub](https://github.com/GovindRajoria) · govindrajoria97@gmail.com
