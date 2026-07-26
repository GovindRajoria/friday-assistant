# Design notes

Why FRIDAY is built the way it is. The [README](../README.md) covers what it
does, how to run it, and how to write a skill; this covers the decisions behind
those, including the ones that look like workarounds until you know what they
are working around.

## ReAct rather than function calling

Ollama exposes a `tools` parameter, and Llama 3.1 supports structured tool
calls. Using it would replace most of `core/brain.py` with a schema and a
loop over `response.message.tool_calls`. Prompt-parsed ReAct was chosen anyway.

**The reason is the `Thought`.** ReAct makes the model's plan a first-class
string in the transcript, before the call happens. `core/main.py` speaks it
aloud (`self.speaker.speak(decision.get("thought"))`) so you hear *"I will scan
the room, then check the volume, then look up the benchmark"* before anything
executes. With native tool calling the plan is not in the output — there is a
function name and an argument object, and the reasoning that produced them is
gone. For an assistant whose only interface is speech, narrating intent before
acting is not a nicety; it is the entire feedback channel.

Two smaller reasons: the tool-call API shape differs between providers, whereas
a text protocol works against anything that emits text; and a malformed
`Action:` line is visible and debuggable in a way a rejected schema is not.

**What it costs:** everything below. Native tool calling would make the next two
sections unnecessary.

## Truncating at `PAUSE`

The protocol asks the model to emit `Thought → Action → Action Input → PAUSE`
and stop. Models do not reliably stop. Left alone, Llama writes its own
`Observation: 1 person detected.` and keeps reasoning against data it invented —
producing a confident final answer built on a webcam frame that was never
captured.

`brain.py` handles this with one line, in both `analyze_intent` and
`resume_react`:

```python
if "PAUSE" in response_text:
    response_text = response_text.split("PAUSE")[0] + "PAUSE"
```

**Why truncation rather than validation.** A parser that detected a fabricated
`Observation:` and rejected the response would have to do something with it:
retry, or fail. Retrying costs a full generation and often produces the same
hallucination, because the prompt that caused it has not changed. Truncation
costs nothing and cannot fail — the fabricated text is physically removed before
anything reads it, so there is no bad state to recover from.

It also survives partial compliance. A model that emits a correct action *and
then* invents an observation is doing the right thing and the wrong thing in one
response; truncation keeps the first and discards the second, where rejection
would throw both away.

**What it does not catch:** a model that never emits `PAUSE` at all. That
response falls through to the `Action:`/`Final Answer:` checks, and if it matches
neither, the loop asks again until `max_react_steps` is exhausted.

## `max_react_steps` is a hard bound, not a retry budget

`analyze_intent` loops at most `max_react_steps` times and then returns *"I got
stuck thinking about that and had to abort the process."* This is deliberately a
plain answer rather than an error: the failure surface is a voice, and a
traceback read aloud helps nobody.

## Skill discovery by walking the filesystem

`core/main.py` does `skills_dir.rglob("*.py")`, imports each module, calls
`setup()`, and registers whatever comes back under `manifest["name"]`. There is
no registry file, no entry-point group, no decorator.

The gain is real: adding a capability is dropping a file in `skills/`. Nothing
else in the repository has to be edited, which means a skill can be written,
tried, and deleted without leaving a trace in version control.

**The cost is a silent failure mode**, and it is worth stating precisely:

```python
self.active_skills[skill_name] = skill_instance
```

That is a dict assignment. Two skills declaring `"name": "system_check"` do not
collide — the second one loaded wins, the first vanishes, and nothing anywhere
reports it. Discovery order comes from `rglob`, so which one survives depends on
directory traversal order.

A registry file would make this a merge conflict. Filesystem discovery makes it
invisible. `tools/check_manifests.py` exists to close exactly that gap: it reads
every manifest statically and fails if two names match. That is the trade being
made — keep the zero-friction extension model, and pay for it with a checker in
CI rather than with a registry in the repository.

The same loop swallows import errors:

```python
except Exception as e:
    print(f"[-] Failed to load {module_name}: {e}")
```

That is intentional. A skill needing a missing dependency should drop out of the
registry, not stop the assistant from booting. `media_control` on Linux relies on
this.

## The manifest checker reads, rather than imports

The obvious implementation of a manifest check is to import each skill and
inspect `setup().manifest`. That does not work here, and the reason is
structural rather than incidental:

- `scan_environment` imports `ultralytics` and `cv2`, and its `setup()` raises
  `FileNotFoundError` unless an exported OpenVINO model is on disk.
- `web_search`, `draft_document` and `memory_manager` import `ollama` at module
  scope; `system_check` imports `psutil`; `draft_document` imports `python-docx`.

So an importing checker needs several gigabytes of wheels and a generated model
to verify nine string literals — and fails for reasons unrelated to the thing it
is checking. `ast` needs none of it, which is why CI can run this gate in
seconds on a machine with no GPU, no camera and no model.

**The trade-off, stated because it is a real hole:** a manifest built
dynamically instead of written as a dict literal cannot be read this way. Rather
than skipping such a module, the checker reports it as an error, so the gap
cannot open quietly.

## Protocols hardcoded in the system prompt

`brain.py` embeds behavioural rules directly in the prompt string — the anomaly
rule that mutes audio when a scan sees more than one person *or* fails to see
the laptop, and holds the mute until exactly one person is confirmed again; the
R&D chain that sequences search → analyse → draft → log; the instruction to do
arithmetic internally rather than reaching for a tool.

These are configuration living in code, and it is fair to call that a smell. The
argument for it: they are **prompt engineering, not settings**. Each one was
added in response to an observed failure — the model calling a nonexistent
`calculator` tool, or using `core_identity` as a filler action — and each is
phrased the way it is because other phrasings did not work. Moving them into
`settings.yaml` would invite editing by someone who has not seen the failure the
wording is defending against, and would present prose whose exact form matters as
though it were a tunable.

The same reasoning explains the negative instructions in the shipped manifests
("DO NOT use this to open applications"). The `description` field is the routing
logic — it is the only information the model has when choosing a tool — so it is
written to steer, not to document.

## What CI verifies

Lint with a pinned rule set, `compileall` over `core`, `skills`, `benchmarks`
and `tools`, and `tools/check_manifests.py` on Python 3.10 and 3.12.

`requirements.txt` is deliberately not installed. It pulls `ultralytics`,
`torch`, `faster-whisper`, `pyttsx3` and `pynput` — gigabytes of wheels, several
of which want audio and camera devices a runner does not have. Installing them
would buy a much slower job that still could not exercise the runtime.

**What it does not verify:** the reasoning loop, speech recognition, synthesis,
and every skill's `execute()`. All of those need a model, a microphone, or a
camera, and none is exercised by anything but hand-testing and the batch runner
in `test_suite.txt`. That is the honest state of it.
