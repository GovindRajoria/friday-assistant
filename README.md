# FRIDAY

[![CI](https://github.com/GovindRajoria/friday-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/GovindRajoria/friday-assistant/actions/workflows/ci.yml)

A voice-driven desktop assistant that runs entirely on local hardware. Speech recognition, reasoning, and text-to-speech all execute on-device — there is no cloud API in the loop, and no audio or camera data leaves the machine.

The interesting part is not the voice interface; it is the reasoning loop. FRIDAY runs a **LangGraph state machine** on top of a local Llama 3.1 via Ollama: every decision is structured output against a JSON schema, not parsed free text, so it can chain several tools together to answer one request — search the web, do arithmetic internally, write the result to a document, then commit a note to long-term memory — deciding each step from the outcome of the last one, with a real bound on how long a chain can run.

Built May 2026.

---

## How it works

```mermaid
flowchart LR
  mic(["microphone"]) --> stt["faster-whisper<br/>wake word + STT"]
  kbd(["keypress"]) --> typed["typed input"]

  stt --> reason
  typed --> reason

  subgraph graph["core/graph.py — LangGraph state machine"]
    reason["reason<br/>structured output → thought/action"]
    act["act<br/>skill.execute(params)"]
    guard["anomaly_guard<br/>deterministic mute rule"]
    nudge["nudge<br/>action and answer both empty"]
    abort["abort<br/>steps past max_react_steps"]

    reason -->|"action set, under step bound"| act
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
every connected client.

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

**Voice input is not wired into the server.** The microphone stays with the
console entry point; the socket carries typed prompts only.

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
| `web_search` | Scrapes DuckDuckGo Lite and has the model extract the factual answer |
| `manage_memory` | Stores and retrieves facts in a local SQLite vault, synthesising a natural reply on retrieval |
| `draft_document` | Generates prose with the local model and saves it as a `.docx` |
| `launch_application` | Opens desktop applications, with per-OS executable name mapping |
| `media_control` | System volume and playback control (Windows) |
| `system_check` | CPU and RAM utilisation |
| `log_fleet_market_data` | Appends structured rows to a CSV ledger |
| `core_identity` | Answers "who are you" without burning a reasoning step |

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
│   ├── graph.py             LangGraph state machine: reason / act / anomaly_guard / nudge / abort
│   ├── state.py             AgentState TypedDict, the single source of loop truth
│   ├── registry.py          Skill discovery + JSON schema derived from the manifests
│   ├── prompts.py           System prompt and per-turn user message construction
│   ├── llm_client.py        The single Ollama client; every call in the project goes through it
│   ├── nodes/
│   │   ├── reason.py        Structured-output call → thought / action / final_answer
│   │   ├── act.py           Skill dispatch + observation capture
│   │   └── anomaly_guard.py Deterministic mute rule, enforced after every scan
│   ├── config.py            Settings loader with layered fallbacks
│   ├── listener.py          faster-whisper STT + wake word + typed-input fallback
│   ├── speaker.py           pyttsx3 TTS
│   └── interrupt_handler.py Global kill-switch listener
├── server/
│   ├── app.py               FastAPI: /health probe, /ws turn stream, single-flight
│   └── events.py            Typed event envelopes sent over the socket
├── skills/                  Auto-discovered capabilities, grouped by domain
├── benchmarks/              YOLO11 / OpenVINO export and latency measurement
├── tools/
│   └── check_manifests.py   Static manifest validation; what CI runs
├── tests/                   pytest — graph routing, the anomaly guard, the step bound, the server
├── config/
│   └── settings.example.yaml
├── requirements.txt
└── skills.sh                Cross-platform bootstrap
```

Design decisions and the reasoning behind them: [docs/DESIGN.md](docs/DESIGN.md).

---

## Known limitations

Stated plainly, because they are the honest state of the project:

- The anomaly guard is coupled to one skill by name. `route_after_act` in `core/graph.py` only routes to `anomaly_guard` when `action == "scan_environment"`; a second skill producing detections worth guarding on would need that check extended by hand.
- `media_control` sets volume by simulating 50 `volumedown` keypresses and stepping back up, because it assumes Windows' fixed 2% increments. It works, but it is a workaround for driver-state issues rather than a clean solution.
- Speech recognition runs `base.en` on CPU with `int8` quantisation, chosen for latency over accuracy.
- **Nothing that needs a model, a microphone or a camera is tested.** CI lints, compiles, validates every skill manifest, and runs seventeen pytest cases against the graph, the guard and the server — real gates, but all of them run against fake skills and a mocked model client. The reasoning loop against a real model, speech recognition, synthesis, and every skill's `execute()` are exercised only by hand.
- The server has no authentication. It is safe only because it refuses to bind to anything but a loopback address — any process on the machine can drive it, and it can launch applications and write files. Exposing the port would hand those capabilities to the network.
- A turn cannot be cancelled over the socket. The Delete-key interrupt belongs to the console's `pynput` listener; a client that sends a prompt waits for it to finish or aborts on the step bound.
- `test_suite.txt` drives an end-to-end batch runner (`run the test suite`) that logs model output for manual review — useful for regression-spotting, not a substitute for unit tests.
- Structured output narrows how a hallucinated tool call can happen, but it does not eliminate model error generally — `action_input` is an open `object` with no per-skill parameter schema, so a wrong or missing argument inside a valid action is still possible and is not validated before `act_node` calls `skill.execute()`.

---

## Licence

**GNU AGPL-3.0** — see [LICENSE](LICENSE).

This project uses [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) for object detection, which is licensed under AGPL-3.0. Because FRIDAY loads Ultralytics at runtime, the combined work inherits AGPL-3.0 terms; the licence choice here follows from that dependency. If you need permissive licensing, substitute the detector in `skills/vision/scan_environment.py`.

## Author

**Govind Kumar** — AI/ML Developer, Metro Infrasys Private Limited
[GitHub](https://github.com/GovindRajoria) · govindrajoria97@gmail.com
