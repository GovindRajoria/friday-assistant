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
  hud(["microphone<br/>HUD — Speak button"]) --> rec["MediaRecorder<br/>WebM/Opus over /ws"]
  rec --> stt2["faster-whisper<br/>same model, no wake word"]
  stt2 --> box["prompt box<br/>operator reads it and sends"]
  kbd(["keypress"]) --> typed["typed input"]

  stt --> reason
  box --> reason
  typed --> reason

  subgraph graph["core/graph.py — LangGraph state machine"]
    reason["reason<br/>structured output → thought/action"]
    confirm["confirm<br/>human sign-off; denies by default"]
    act["act<br/>skill.execute(params)"]
    guard["anomaly_guard<br/>deterministic mute rule"]
    nudge["nudge<br/>action and answer both empty"]
    abort["abort<br/>steps past max_react_steps"]

    reason -->|"action set, under step bound"| act
    reason -->|"action is destructive"| confirm
    confirm -->|"approved"| act
    confirm -->|"denied — becomes an Observation"| reason
    reason -->|"action = none, no answer"| nudge
    reason -->|"step bound reached"| abort
    act -->|"action = scan_environment"| guard
    act -->|"otherwise"| reason
    guard --> reason
    nudge --> reason
  end

  reason -->|"action = none, final_answer set"| tts["pyttsx3"] --> spk(["speaker"])
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
word. The desktop window records with the browser's `MediaRecorder` when the
**Speak** button or **Ctrl+Shift+Space** is pressed, and sends the WebM/Opus
blob down the WebSocket it already has open; `core/transcriber.py` decodes and
transcribes it with the same faster-whisper model the console uses.
Chromium's own `SpeechRecognition` API is deliberately not used — it uploads
audio to Google, which would quietly end the local-first claim.

**What comes back is put in the prompt box and left there.** It is not run. A
mishearing that goes straight into the graph is a mishearing that can reach a
skill which deletes files; recognition being good is why that is rare, and the
review step is why it does not matter when it happens. Correct the word, press
Enter.

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

| Skill | What it does |
|---|---|
| `scan_environment` | Captures a webcam frame and runs YOLO11 object detection through OpenVINO |
| `web_search` | Looks a fact up across three keyless APIs — DuckDuckGo Instant Answer, MediaWiki, Google News — and has the model extract the answer |
| `read_news` | Current headlines from Google News RSS, general or by topic, for the model to summarise |
| `weather` | Current conditions and a three-day forecast from Open-Meteo, no API key |
| `read_webpage` | Fetches a URL and extracts its readable text, for summarising a link or a story behind a headline |
| `describe_screen` | Captures the screen on request and describes it — a fresh look, distinct from the ambient watcher |
| `clipboard` | Reads what was copied, or copies text onto the clipboard |
| `reminders` | Sets, lists and cancels reminders that FRIDAY delivers on its own, even with nothing open |
| `manage_memory` | Stores and retrieves facts in a local SQLite vault, synthesising a natural reply on retrieval |
| `draft_document` | Generates prose with the local model and saves it as a `.docx` |
| `launch_application` | Opens desktop applications, with per-OS executable name mapping |
| `media_control` | System volume and playback control (Windows) |
| `system_check` | CPU and RAM utilisation |
| `log_fleet_market_data` | Appends structured rows to a CSV ledger |
| `core_identity` | Answers "who are you" from the skills actually loaded, without burning a reasoning step |
| `explain_architecture` | Answers "how do you work" from `docs/ARCHITECTURE.md`, verbatim, rather than from the model's idea of how assistants work |
| `manage_files` | Lists, reads, moves and deletes inside an allowlisted workspace — **destructive**, confirmed |
| `window_control` | Lists, focuses, minimises and maximises desktop windows through `user32` |
| `send_keys` | Types text or presses a hotkey combination — **destructive**, confirmed |

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
│   │   └── anomaly_guard.py Deterministic mute rule, enforced after every scan
│   ├── config.py            Settings loader with layered fallbacks
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
├── benchmarks/              YOLO11 / OpenVINO export, and stt_models.py for the STT comparison
├── tools/
│   └── check_manifests.py   Static manifest validation; what CI runs
├── tests/                   pytest — graph routing, the anomaly guard, the step bound, the server
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

- The anomaly guard is coupled to one skill by name. `route_after_act` in `core/graph.py` only routes to `anomaly_guard` when `action == "scan_environment"`; a second skill producing detections worth guarding on would need that check extended by hand.
- `media_control` sets volume by simulating 50 `volumedown` keypresses and stepping back up, because it assumes Windows' fixed 2% increments. It works, but it is a workaround for driver-state issues rather than a clean solution.
- **Speech recognition is a latency compromise and the accuracy half is unmeasured on real voices.** `small.en` on CPU at `int8` was chosen because every larger model measured at or past realtime on this machine, which push-to-talk cannot absorb. The comparison that picked it used synthesised speech, so it establishes the timings and nothing about word error on an accented voice in a room — run `benchmarks/stt_models.py --record` to settle that for yourself, and raise `audio.stt_model` if you would rather wait.
- **The voice path is verified in two halves that have not yet been joined end to end.** The server half: a real WebM/Opus blob sent over the socket to a live backend came back correctly transcribed in 3.6s, and started no turn. The renderer half: pressing the control really does open the microphone and put the HUD into its recording state, observed in the live DOM. What has not been measured is a spoken sentence going in one end and the right words coming out the other — and none of the renderer audio code (`getUserMedia`, `MediaRecorder`, the permission prompt, the global hotkey) has any automated coverage. Talking to it is the only check that counts.
- **Web lookup depends on third-party endpoints that can close without warning.** `web_search` originally scraped DuckDuckGo's HTML; on 2026-08-03 both the lite and html endpoints began answering 202 with zero results, and every web question failed as "I couldn't find any results" — indistinguishable from an empty search. It now uses documented APIs and falls through three sources rather than one, which makes a single outage survivable, not impossible. `read_news` is localised to India/English in `LOCALE`; change those two values for another region.
- **The model will claim to have done things it has not done.** Caught live: asked to set a reminder, it replied "Reminder set" without calling the skill, and nothing was stored — the worst available failure for a feature whose value is that you stop holding the thing in your head. The system prompt now states that "done", "saved" and "reminder set" are true only when a tool just said so in an Observation. That is an instruction, not a guarantee.
- **The model will still answer a current-events question from memory if allowed to.** Caught live: asked to summarise today's news, it made no tool call and invented plausible headlines. The system prompt now carries an explicit rule that anything time-sensitive must come from a tool result, and the graph refuses to re-run a tool call identical to the one it just made — but both are instructions and a guard, not a guarantee that a local model never confabulates.
- **Nothing that needs a model, a microphone or a camera is tested.** CI lints, compiles, validates every skill manifest, and runs one hundred and twenty-eight pytest cases against the graph, the guard, the confirmation gate, the path allowlist, the transcription path and the server — one hundred and twenty-seven on a machine that cannot create symlinks, where one allowlist case skips — real gates, but all of them run against fake skills and a mocked model client. The reasoning loop against a real model, speech recognition, synthesis, and every skill's `execute()` are exercised only by hand.
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
