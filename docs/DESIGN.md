# Design notes

Why FRIDAY is built the way it is. The [README](../README.md) covers what it
does, how to run it, and how to write a skill; this covers the decisions behind
those, including the ones that look like workarounds until you know what they
are working around.

## ReAct rather than function calling

Ollama exposes a `tools` parameter, and Llama 3.1 supports structured tool
calls. Using it would have replaced most of what was `core/brain.py` with a
schema and a loop over `response.message.tool_calls`. Prompt-parsed ReAct was
chosen instead — originally for three reasons, one of which still holds
exactly as stated and two of which did not survive what replaced the protocol
(see the supersession below).

**The reason that still holds is the `Thought`.** ReAct makes the model's plan
a first-class string in the transcript, before the call happens. The old
`core/main.py` spoke it aloud before anything executed, so you would hear *"I
will scan the room, then check the volume, then look up the benchmark"*
before the tool fired. With native tool calling the plan is not in the
output — there is a function name and an argument object, and the reasoning
that produced them is gone. For an assistant whose only interface is speech,
narrating intent before acting is not a nicety; it is the entire feedback
channel. Nothing about that has changed: `core/nodes/reason.py` still
produces a `thought` before `act` runs, and `core/main.py:69` still speaks
each `narration` entry as the graph streams it.

Two smaller reasons argued for the text protocol at the time: the tool-call
API shape differs between providers, whereas a text protocol works against
anything that emits text; and a malformed `Action:` line is visible and
debuggable in a way a rejected schema is not. Neither survived contact with a
model that reworded a line and dropped a tool call silently — see the commit
this file now documents.

### Supersession: structured output with a required `thought`

The framing above treated this as a straight choice between two options — a
free-text protocol that keeps the thought, or native tool calling that drops
it. There is a third option, and it is what the system runs now: ask Ollama
for structured output (its `format` parameter, which takes an arbitrary JSON
Schema) rather than its `tools` parameter, and put `thought` in that schema as
a required field alongside `action`. `core/registry.py:build_action_schema`
does exactly this — see "The structured-output schema" below for the schema
itself and what deriving it actually took. The model still narrates, because
the response will not validate without a non-empty `thought` string, and
routing now reads `decision["action"]` directly instead of pattern-matching
an `Action:` line, so a reworded response cannot silently drop a tool call.

This was not the obvious next step, and it is worth saying why. The `tools`
parameter and the `format` parameter are two different mechanisms Ollama
exposes, not two settings on the same one: a `tools` call returns a function
name and an argument object and nothing else, structurally incapable of
carrying a free-text field, while a `format` call returns whatever shape the
schema describes, including a narration field sitting next to the enum. The
original rejection was written against `tools` specifically and was correct
about `tools` specifically; it did not generalize to "no schema can carry a
thought," and confusing the two would have meant living with the brittle text
protocol indefinitely.

## Truncating at `PAUSE`

**Closed.** This section is kept as the record of a real problem, because the
next thing that happened only makes sense against it. The structured-output
schema in `core/registry.py` closes the whole class of failure below: a
schema-constrained response has no free-text slot left to keep writing into
after `action` and `action_input` are filled, so it cannot contain a
fabricated `Observation:` line at all. The mechanism described here is
deleted code (it lived in `core/brain.py`, removed with this commit).

The old protocol asked the model to emit `Thought → Action → Action Input →
PAUSE` and stop. Models did not reliably stop. Left alone, Llama would write
its own `Observation: 1 person detected.` and keep reasoning against data it
invented — producing a confident final answer built on a webcam frame that
was never captured.

`brain.py` handled this with one line, in both `analyze_intent` and
`resume_react`:

```python
if "PAUSE" in response_text:
    response_text = response_text.split("PAUSE")[0] + "PAUSE"
```

**Why truncation rather than validation, at the time.** A parser that detected
a fabricated `Observation:` and rejected the response would have had to do
something with it: retry, or fail. Retrying costs a full generation and often
produces the same hallucination, because the prompt that caused it has not
changed. Truncation cost nothing and could not fail — the fabricated text was
physically removed before anything read it, so there was no bad state to
recover from.

It also survived partial compliance. A model that emitted a correct action
*and then* invented an observation was doing the right thing and the wrong
thing in one response; truncation kept the first and discarded the second,
where rejection would have thrown both away.

**What it never caught:** a model that never emitted `PAUSE` at all. That
response fell through to the `Action:`/`Final Answer:` checks, and if it
matched neither, the loop asked again — which is the failure mode the next
section was written about, and had overclaimed about its own fix.

## The structured-output schema

`core/registry.py:build_action_schema` derives a JSON Schema from the loaded
skill manifests: `action` is an enum of every loaded skill's name plus the
sentinel `"none"`, and all four fields — `thought`, `action`, `action_input`,
`final_answer` — are required. Three things about that shape were not
predictable from the design on paper. All three were found by running the
real model against real schema variants on this machine, not by reasoning
about the schema in the abstract.

**1. Making `action` optional did not work.** The first schema tried left
`action` out of `required`, on the reasoning that a model choosing not to act
should be free to omit it. Feeding that schema to llama3.1 produced a
`thought` and nothing else — the model narrated a plan and selected no
action, on the first live call. A field that is legal to omit gets omitted;
requiring `action` and giving it an explicit `"none"` value in the enum is
what turns "decline to act" into a choice the model has to make rather than a
field it can leave blank.

**2. Requiring `final_answer` means the model fills it even when an action is
pending.** With all four fields required, llama3.1 returned a schema-valid
response that both called a tool and answered the question in the same
breath:

```json
{"thought": "...", "action": "scan_environment", "action_input": {},
 "final_answer": "I can see the following: [list of environment details]"}
```

`final_answer` there is a placeholder, written before the scan had run,
because the schema does not let the field stay empty. `core/graph.py`'s
`route_after_reason` tests `action` before `final_answer` for exactly this
reason — reading `final_answer` first would speak that placeholder and never
call the tool — and `core/nodes/reason.py`'s `reason_node` discards the field
at the source, setting `final_answer` to `""` whenever `action != "none"`
rather than passing through whatever the model wrote. The two are
belt-and-braces on the same observed failure: one keeps the placeholder out
of state, the other keeps it from being routed on if it gets in anyway.
`tests/test_placeholder_answer.py` is the regression test for this exact
payload.

**3. The prompt has to name the `"none"` sentinel.** The old text protocol
ended a turn with `Final Answer:`, which doubled as the model's cue that it
was allowed to stop. Dropping that line and requiring `action` left nothing
describing how to decline: the schema forces a value, and with no instruction
telling the model that `"none"` was a legal, ordinary answer, it selected a
tool on every single turn. Five consecutive live runs — one of them told
explicitly not to use a tool — ran to the step bound and aborted before this
was diagnosed. The fix was a prompt rule, not a schema or code change; it now
reads, in `core/prompts.py`:

> TERMINATION: When you have enough information to answer, set `action` to
> `"none"` and put your complete reply in `final_answer`. `"none"` is the
> correct choice for any request that needs no tool — a greeting, a question
> you can already answer, or a chain that has finished.

An enum constrains what the model can select; it does not tell the model
which value means "stop," and that turned out not to be discoverable from the
schema alone. This is the same category of fact "Protocols hardcoded in the
system prompt" (below) has always made about the rest of the prompt — it took
a schema migration to produce a new instance of it.

## `max_react_steps` bounds the whole chain — it did not always

**This section previously overclaimed, and it is worth correcting plainly.**
It used to read as though `max_react_steps` bounded the reasoning loop end to
end. It did not. `analyze_intent` in the old `core/brain.py` looped at most
`max_react_steps` times, but only across its own malformed-output resample —
retries of a single decision, not steps of a chain. The chaining path,
`resume_react` called repeatedly from the `while` loop in `core/main.py`, had
no counter of its own and exited only on a `final_answer`, an `unknown`
route, or a Delete keypress. A model that kept emitting well-formed actions
would chain forever; nothing but an interrupt or a final answer stopped it.
README:23 and README:49 both stated a bound that did not exist on that path —
corrected there along with this file.

It is a real bound now. `core/state.py`'s `steps` field increments once per
pass through `reason_node`, and `core/graph.py`'s `route_after_reason` checks
it against `SETTINGS["llm"]["max_react_steps"]` before allowing another
`act`; past the limit it routes to `abort_node`, which returns a plain
sentence — *"I worked through several steps without reaching a conclusion, so
I stopped."* — rather than a traceback. That choice survives from the
original design intent even though the mechanism did not: the failure
surface is a voice, and a stack trace read aloud helps nobody.
(`core/main.py` has its own fallback for the case where the graph ends with
no `final_answer` at all — *"I worked through that but have no answer to
give."* — a distinct message for a distinct condition.)

The number changed too, 5 to 12. 5 was sized as a resample budget for a single
malformed reply, never as a chain length, and a proactive agent that scans
the room and checks system state before answering spends several steps on
state awareness alone before it does anything else. There is a second,
coarser ceiling underneath the step counter: `core/main.py` calls
`graph.stream(state, {"recursion_limit": 40})`, LangGraph's own guard against
unbounded node transitions. It is not sized to a precise multiple of
`max_react_steps` — a scan turn costs three graph steps (`reason`, `act`,
`anomaly_guard`) against one increment of `steps` — so the two bounds are not
guaranteed to trip in a fixed order in every scenario; `max_react_steps` is
the one meant to govern ordinary operation, and `recursion_limit` is the
backstop under it.

## Skill discovery by walking the filesystem

`core/registry.py:discover_skills` does `skills_dir.rglob("*.py")`, imports
each module, calls `setup()`, and registers whatever comes back under
`manifest["name"]`. This lived in `core/main.py` before the graph migration
and moved out verbatim — same `rglob` + `importlib` + `setup()` contract, same
behavior, a different file. There is no registry file in the sense of a
hand-maintained list, no entry-point group, no decorator.

The gain is real: adding a capability is dropping a file in `skills/`. Nothing
else in the repository has to be edited, which means a skill can be written,
tried, and deleted without leaving a trace in version control.

**The cost is a silent failure mode**, and it is worth stating precisely:

```python
active_skills[skill_name] = skill_instance
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

The old `brain.py` embedded behavioural rules directly in the prompt string,
and `core/prompts.py` still does — minus one of them. The anomaly rule (react
when a scan sees more than one person *or* fails to see the laptop, and hold
that state until a scan reports exactly one person *and* the laptop together
again) used to be a sentence in that string with no enforcement behind it at
all. It is not prompt text anymore: `core/nodes/anomaly_guard.py` runs the same
rule in Python after every scan whether or not the model cooperates, and its two
halves are two functions, `_is_anomalous` and `_is_clear`, each testable with a
dict of counts and no model in the loop.

## Reversed: the privacy guard no longer mutes by default

The rule above originally muted system audio whenever it fired. That part is now
`privacy.auto_mute`, and it defaults to **false**.

The reversal came from the operator, in the plainest possible terms: *"it mutes
the volume when it detects more than 2 person but i didn't told it to do that."*
The rule was not wrong about what it saw and the enforcement was not buggy. What
was wrong was the shape of the decision. Muting the machine is an intervention
with an audible consequence and no visible cause, arrived at by inference from a
webcam frame, and it was switched on for everybody because it seemed prudent when
it was written. Prudent is not the same as chosen.

So the two halves are split. Detection stays exactly as deterministic as it was —
that was never the complaint, and weakening it would have been the wrong fix.
What changes is that the default response is to *say* what it noticed and touch
nothing. `privacy.auto_mute` restores the old behaviour for anyone who wanted it;
`privacy.announce_only` set false switches the guard off completely.

Two smaller things fell out of it, both of the same kind — the guard was making
claims it had not checked:

- The narration said "Audio muted until the area is clear" **unconditionally**,
  discarding the skill's return value. With `media_control` unloaded or failing,
  FRIDAY reported an intervention that never happened. It now narrates what the
  call actually returned.
- `media_control` reached for the CoreAudio COM interface and, when that
  appeared unavailable, fell back to pressing the mute **media key**. The key is
  a toggle, so "mute" applied to an already-muted machine unmuted it, and the
  guard's latch could disagree with the real audio state. The COM path was in
  fact never available: newer `pycaw` returns a wrapped `AudioDevice` from
  `GetSpeakers()` rather than the raw `IMMDevice`, so the code's check for an
  `Activate` method failed every time and silently took the fallback on every
  call. Both shapes are handled now, the state is verified with `GetMute()`
  after being set, and the keypath is only used when the current state can be
  read and is wrong. A state that cannot be read is reported as a failure
  instead of guessed at — an unverifiable toggle is worse than an honest refusal
  when the thing being toggled is whether the machine can be heard.

The lesson generalises past audio: a deterministic guard is the right way to
enforce a rule, and it does not by itself make the rule's *content* someone
else's choice. `enabled` on the proactive layer had already been argued this way;
this one had to be retrofitted.

What is still true, and argued the same way as before: the R&D chain that
sequences search → analyse → draft → log, and the instruction to do
arithmetic internally rather than reaching for a tool, both remain in
`build_system_prompt`. These are configuration living in code, and it is fair
to call that a smell. The argument for it: they are **prompt engineering, not
settings**. Each one was added in response to an observed failure — the model
calling a nonexistent `calculator` tool, or using `core_identity` as a filler
action — and each is phrased the way it is because other phrasings did not
work. Moving them into `settings.yaml` would invite editing by someone who has
not seen the failure the wording is defending against, and would present prose
whose exact form matters as though it were a tunable.

The graph migration produced a fresh instance of this same argument rather
than making it obsolete. Dropping the old `Final Answer:` exit condition left
nothing describing how to stop; with `action` now a required schema field,
that omission meant the model picked a tool on every turn and every request
ran to the step bound — five consecutive live runs, one of them told
explicitly not to use a tool. The fix was not a schema change; it was one
prompt line (`OPERATING RULES: 1. TERMINATION`, in `core/prompts.py`) naming
the `"none"` sentinel explicitly, because a value the model must select from
an enum is not discoverable from the enum alone. See "The structured-output
schema" above for the full account — it belongs in both places, because it is
simultaneously a fact about the schema and a fresh example of a prompt
sentence that exists only because a specific failure demanded it.

The same reasoning explains the negative instructions in the shipped manifests
("DO NOT use this to open applications"). The `description` field is the routing
logic — it is the only information the model has when choosing a tool — so it is
written to steer, not to document.

## Forty-six skills, and what more tools cost

The skill count went from 19 to 46 in one batch. The capabilities were the easy
part; two things about that number were not obvious in advance and both had to be
answered in code.

**The `description` field stopped being documentation and became the load-bearing
surface.** It always was the routing logic — it is the only information the model
has when choosing — but with 19 skills the overlaps were rare enough to ignore. At
46 they are structural: `read_document` against `manage_files`, `ocr_screen`
against `describe_screen`, `search_files` against `search_code`, `diagnose_self`
against four narrower status skills. `tools/check_manifests.py` cannot catch this,
because a description that is present, 40 characters long, and *indistinguishable
from its neighbour* passes every check while quietly sending the model to the wrong
tool. So every competing pair now names the other explicitly — the pattern
`explain_architecture` established with "Use `core_identity` instead for WHAT you
can do" — and `tests/test_skill_routing_surface.py` pins those disambiguations so a
later edit cannot drop one silently.

**Reach was split into three allowlists rather than one.** `filesystem.allowed_roots`
is a workspace where files are written and deleted; `projects.allowed_roots` is
source trees that may only be read; `commands.allowed_roots` plus
`allowed_executables` is where a program may be run. Collapsing any two would mean
that permission to describe a repository implied permission to run a test suite in
it, which is a different question with a different answer. The two new lists start
empty, so a fresh install refuses and says what to configure — an allowlist that
begins closed cannot be forgotten about.

`skills.disabled` exists because none of this is measured. There is still no
tool-selection accuracy number for this project, so the honest position is that
nobody knows whether 45 tools route better or worse than 19 — and a group that
turns out to confuse routing has to be switchable off in settings rather than
revertable only by a merge.

## More tools made conversation worse, and prompt rules did not fix it

The first live session after the batch landed asked "Hello, friday." and got:
`describe_screen`, `describe_screen` again, `read_webpage`, `read_webpage` again,
three `web_search` calls, then `core_identity` — answered with a list of all 44
other tools. Twenty-odd steps to say hello.

Every one of those steps broke a rule that was already in the system prompt.
OPERATING RULE 2 has said since Phase 1 that `"none"` is correct for a greeting.
Rule 4 says `core_identity` is for capability questions and "do not call it for
anything else". The rules were not missing, unclear, or new. They were ignored,
and they were ignored *more* than before, because 45 plausible actions is 45 ways
to avoid simply answering.

That is the third time this project has reached the same conclusion — the anomaly
rule, the `terminal` flag, and now this. **When asking has failed repeatedly, the
fix is not better wording. It is making the wrong move unavailable.**

So a conversational message never enters the reasoning loop. `core/small_talk.py`
decides in Python whether the whole message is conversation, and the graph's entry
point routes it to `core/nodes/converse.py`, which calls the model with **no schema
and no tool list**. There is no `action` field to fill in, so a tool call is not
something it can produce. The reply is still generated, not canned — a fixed
"Hello, Sir." would have closed the bug and made the assistant worse.

The classifier is deliberately narrow, because the failure modes are not
symmetrical: a message it wrongly matches becomes unanswerable, while a message it
wrongly declines merely costs a normal turn. One substantive word anywhere and it
declines. "hi, what's the weather", "thanks, now delete that file" and "hello, how
do you work?" all reach the reasoning loop.

**A second failure the same session showed the step bound was the wrong bound.**
"What is 15 percent of 240" — arithmetic that PERMANENT COGNITIVE GUARDRAIL 1
explicitly forbids using a tool for — spent `web_search`, `describe_screen`,
`run_command` and then *nine consecutive* `manage_settings` calls with varying
parameters, so the identical-repeat guard never fired once, and hit the step bound
37 seconds later with no answer at all. `steps` counts reasoning passes, so it
tolerates a dozen tool calls before tripping.

A turn now gets five tool calls, and three consecutive calls to one tool. Past
either, it routes to `core/nodes/conclude.py` — the model, the transcript, no tools
— rather than to `abort`, whose reply is an apology. By that point the answer is
usually already in the transcript; what was missing was a turn in which answering
was the only option. Measured on the same model and inputs: the greeting went from
twenty-plus calls to zero in 0.9s, and the arithmetic from 12 calls and no answer
to 4 calls and "36".

Four calls is still three more than that question deserves. Recorded rather than
claimed as fixed.

## The wake word, and why the recorder never stops

Push-to-talk was the first voice input because it is unambiguous: the operator
pressed something, so the audio that follows is meant for the assistant. Asking to
be listened to continuously replaces that certainty with a guess, and everything
below is about making the guess cheap to get wrong in the right direction.

**Why not a dedicated wake-word model.** openWakeWord is the correct tool: a tiny
always-on network at one or two percent of a core, so nothing is transcribed until
it fires. It was rejected for this build for a specific reason rather than a vague
one — it ships no pretrained "friday". The available words are "hey jarvis",
"alexa", "hey mycroft" and similar, and training a custom one is a separate
project with its own dataset. Given the choice between the right architecture with
the wrong name and a heavier approach that answers to "Friday", the name won. The
cost is stated in the README: every utterance in the room is transcribed locally
before it can be discarded, at roughly a quarter of a core while anyone is
talking. If a "friday" model ever exists, this is the piece to replace.

**Why the recorder is never started on demand.** The obvious implementation
watches the microphone's energy, starts recording when speech begins, and stops
when it ends. That loses the beginning of the first word, because detection needs
a couple of hundred milliseconds of evidence before it can be sure — and in this
design the first word is the one that decides whether anything happens at all.
`riday, what's the weather` reads as a broken microphone. So the recorder is
always running and is *cut* during silence: speech onset is captured because
capture began before the speech did, and the only gap is between one recorder
ending and the next beginning, which by construction falls inside a silence.

**Why the threshold moves.** A constant is deaf in a quiet room and permanently
triggered in a noisy one, and the same laptop is both across a day. The noise
floor is tracked as an exponential average of the level measured *while nobody is
speaking*, and the trigger sits a fixed multiple above it. Updating the floor
during speech would be the bug that eats itself: a long sentence would drag the
floor up past the speaker's own voice and the assistant would go deaf mid-request.

**Why the matching is asymmetric.** `core/wake_word.py` is generous about what
counts as the name and strict about where it appears, and that shape comes
directly from the costs. A missed wake word costs one repetition, so the variant
list and the fuzzy ratio are wide enough to accept the mishearings `small.en`
actually produces for a proper noun on an accented voice. A false trigger runs a
turn on a sentence somebody said to another person, so the name must appear in the
first few words with only filler before it. "Thursday" was in the variant list
during development and was removed for exactly this reason: it is a plausible
mishearing of "Friday" *and* a real word people say to each other constantly, and
the second fact outweighs the first.

**Why this mode acts without review.** The prompt-box review exists because a
misheard sentence can reach a skill that deletes files. Auto-submitting on a wake
word does not disagree with that; it substitutes a different gate. Pressing a
button and speaking produces audio the operator recorded, which is weak evidence
about intent. Saying the assistant's name is an explicit act of address, which is
stronger. And the layer that actually stops damage was never the review step — it
is the confirmation gate, which is untouched, so the worst an unreviewed
mishearing reaches on its own is a read-only skill.

**Why the review step went entirely (2026-08-07).** The paragraph above ends up
proving more than it set out to. If the confirmation gate is the layer that stops
damage, and an act of address is what distinguishes a request from ambient audio,
then a press is an act of address too — a deliberate one — and the review step
was buying very little at the cost of making speech slower than typing. The
operator's own verdict was blunter: *"what's the point of speaking if I have to
click to send that."* Both microphone paths now run what they heard.

The two paths still differ in what they require, and it is not an inconsistency.
Push-to-talk needs no name because the press is the address; ambient mode does,
because a microphone open for an hour has nothing else to separate a request from
the room. They also differ in what they can *hear*: `useMicrophone` releases the
device inside `recorder.onstop`, before the recording is even sent, so
push-to-talk cannot capture the answer to its own question. Ambient mode's
microphone is open while the answer plays, and the platform voice plays out of
process where `echoCancellation` on the capture stream cannot reach it — so in
that mode the wake word is also the only thing stopping the assistant from
hearing itself and replying to it. Relaxing it needs a temporal gate in its place.

**Why the fix for routing was fewer tools rather than better descriptions.** This
is the one the project spent the longest getting wrong, and the record of the
wrong turns is more useful than the answer.

Tool selection measured 56% with forty-six skills loaded. Four attempts to improve
it by giving the model *more* information all failed, and each failed in an
instructive way. A schema field asking whether a tool was needed before choosing
which — three variants, one of them taking routing to 39.7%, because the model
writes the whole object as a script of a finished interaction rather than as a
classification of the request. Sharper descriptions on the losing skills, which
worked per skill and moved the attractor to their neighbours. A negative clause
naming the cases that had been stolen, which changed the score by exactly nothing.

The clue was there the whole time and was easy to misread: several skills that
*lost* a routing decision already carried a description naming the skill that
should have won. `system_check` says "gpu_status for accelerators" and still beat
`gpu_status` on "is the GPU being used". The information was present, correctly
worded, in the prompt, and not used — which is a statement about attention, not
about content. Forty-seven descriptions is roughly eight thousand tokens of JSON.

Offering about ten scored skills instead of all forty-seven took routing from
**70.5% to 84.6%**, like-for-like, with no description changed. Every long-running
miss cleared at once, including the arithmetic case that had survived three schema
redesigns: asked how many bytes are in a gigabyte, it now answers without a tool.

Three decisions inside it are worth keeping:

- **Lexical scoring, weighted by inverse document frequency over the manifests.**
  There is no embedding model installed on this machine, and adding one to rank
  forty-seven short documents would be a large dependency for a small job. IDF is
  what makes the lexical version work at all — it prices "use" and "this" at zero
  because every manifest contains them, and "clipboard" highly because one does.
- **No confident match, no shortlist.** If nothing scores above a floor the whole
  registry is offered. The enum is built from the same subset, so a skill left out
  is not merely ranked badly, it is unnameable — and a ranking built on noise is
  strictly worse than no ranking.
- **The size was measured, not chosen.** Every case in the labelled set keeps its
  correct answer inside a list of eight; ten ships for margin, and a test asserts
  that recall with no model running, so it is a CI gate rather than a benchmark.

Two descriptions were rewritten because that recall test failed on them, which is
the opposite of the earlier tuning: `system_check` contained no occurrence of the
word "machine" and `manage_memory` none of "remember", so the only words anybody
says when asking for them appeared nowhere in the text being ranked. Adding a word
the skill genuinely is about is not the same as a skill claiming ground next to it.

**What it changed about the remaining errors, which matters more than the score.** All three
regressions were diagnosed, and in every one the correct skill was offered and the model chose
something else — `task_list` ranked first by more than double the next score and was still declined
in favour of "none". So what is left is not a retrieval problem but a selection problem among about
ten candidates. That is worth stating precisely because it changes what to try next: description
steering between competing pairs was measured as useless at forty-seven skills, where it moved the
attractor rather than removing it, but the reason it failed there does not apply to a field of ten.

**What this does not fix.** The shortlist is computed from the operator's words
alone, so a chain whose second step needs a tool the original sentence did not
imply can find it missing. No case in the labelled set does that, and the cheap
answer — widen the list after the first step, since growing an enum is safe where
shrinking it is not — is deliberately unbuilt, because there is no measurement
saying it is needed.

**Why the clock is in the prompt and not only a skill.** Nothing in this project
reported the time until August 2026 — no skill, and no date anywhere in either
prompt. "What time is it" is close to the most common thing anyone says to an
assistant, and it scored 0 out of 3 in the routing benchmark. The interesting
part is that routing was not confused: there was nothing to route to, so the
model answered from training data, which for a clock means answering wrongly.

Most of the fix is one line in `build_user_message`, because asking a language
model to invoke a tool to discover what day it is would be a lot of machinery
for a value the process already holds. But injecting it is not sufficient on its
own: OPERATING RULE 1 says "You have no knowledge of today... you MUST call a
tool first", which is exactly correct for news and weather and exactly wrong for
this. The timestamp needed a matching carve-out in that rule, or the model would
have kept hunting for a tool while holding the answer.

`world_time` remains, for the two things a static timestamp cannot answer:
another timezone, and arithmetic between dates. Its offsets are reported in hours
and minutes rather than decimals — a sixth of the world is not on a whole-hour
offset, including where this assistant runs, and the 45-minute gap between
Kolkata and Kathmandu rendered as "0.2 hours ahead" before that was fixed.

**Why `set_volume` was rewritten rather than tuned.** It pressed `volumemute`
twice "to wake the driver", then `volumedown` fifty times to reach zero, then
`volumeup` up to fifty times to climb to the requested level, then played a
1000 Hz beep at the operator. Every part of that is a problem: a hundred
keystrokes take a visible moment and land on whatever window has focus, and the
level reached depends on an unverified assumption that the media key steps by 2%.
`_apply_mute` had already established the CoreAudio path on this machine, and
`SetMasterVolumeLevelScalar` sets a level exactly, instantly, with no keystrokes.
Where that interface is unreachable, an absolute level now reports honestly
instead of approximating one — the same choice the mute path already makes about
a state it cannot read. The media keys are kept for a *relative* step, where
there is no target state to get wrong, only a direction.

**Why the follow-up window is timed rather than filtered.** Letting the next
sentence follow without the name is what makes hands-free feel like a
conversation instead of a command line, and it is also the change most likely to
produce a machine that talks to itself forever. The naive version removes the
only protection against that at precisely the moment audio is playing.

The first design that suggests itself is a text filter: drop anything that looks
like what was just said. It cannot be the primary gate. There is no threshold
separating "an echo of my answer" from "the operator repeating my words back at
me" — which people do constantly, because replying with the words you just heard
is how conversation works. Measured at 0.75 similarity, *"what is the weather in
Bhopal"* scores as an echo of *"the weather in Bhopal is warm"*. So the gate is
temporal and the filter sits behind it, covering only the case the timing cannot:
the operator talking over the answer, so one recorded segment holds both voices.

Two things about the timing were wrong in the first plan for it, and both matter:

- **It must run from playback finishing, not from the turn ending.** The answer
  is handed to a queue and `_run_prompt` returns immediately, so the speakers are
  frequently still going seconds after the turn is over. `runAndWait()` having
  returned is the only trustworthy signal on this platform, which is why the
  speech thread publishes it.
- **It must be measured against when the audio was recorded, not when it
  arrived.** This is the one that would have shipped broken. Transcription takes
  one to two seconds, so by the time an utterance is evaluated, an echo has
  already aged past any grace interval anyone would pick — the gate would admit
  exactly what it was written to exclude. The recording time is stamped the
  moment the binary frame lands, which is within milliseconds of the segment
  being cut, and that only holds because the HUD ships a segment straight out of
  `recorder.onstop` with nothing slow in between. Adding an `await` to that path
  would reopen the hole silently, so it is named in the docstring.

The grace interval before the window opens is arithmetic, not preference: a
segment that recorded the tail of playback is not cut until `SILENCE_MS_TO_END`
of silence has passed, so it ends *after* the audio did. The grace must exceed
that. Those two constants are in different languages in different directories,
and raising `SILENCE_MS_TO_END` to stop cutting people off at commas is a
plausible future change that would quietly reopen the loop — so a test reads the
TypeScript and asserts the relationship.

**Why one control replaced two.** The command bar had **Speak** and **Wake
word** side by side, and the operator's complaint about the arrangement was the
arrangement. Two controls for one capability make somebody decide which kind of
microphone they want before they have said anything — a question about this
program's internals wearing the clothes of a question about their intent. There
is one control now, showing off / listening / hearing you / working, and
push-to-talk kept its hotkey and lost its button. `src/voice.ts` derives that
state in a pure function so the precedence between those states can be asserted;
the case worth asserting is that a running turn outranks "hearing you", because
the open microphone genuinely is picking up the assistant's own voice at that
moment and saying so reads as a malfunction.

Dropping the button did leave one real hole: if another application already
holds `Ctrl+Shift+Space`, push-to-talk had no way in at all. The renderer now
listens for the same combination itself, which covers exactly the case the
global registration cannot — this window having focus.

## What CI verifies

Lint with a pinned rule set, `compileall` over `core`, `skills`, `benchmarks`
and `tools`, `tools/check_manifests.py` on Python 3.10 and 3.12, and now
`pytest` against `FRIDAY_CORE/tests/` — 728 tests covering graph routing, the
anomaly guard and its privacy switch, the step bound, the confirmation gate, the
path allowlist, the volume and mute paths with the COM layer stubbed, the server,
the speech pipeline and the follow-up window with the engine faked, and a
regression test for the placeholder-answer failure described above, all run
against fake skills and a mocked `llm_client.chat`, no model required.

One test reaches outside Python: `test_follow_up_window.py` reads
`useAlwaysListening.ts` and asserts that the follow-up grace interval still
exceeds the segmenter's silence window. Those two constants have to hold a
relationship and live in different languages, and the failure mode if they drift
is the assistant answering its own voice, so the coupling is worth a file read.

`requirements.txt` is still deliberately not installed in full. It pulls
`ultralytics`, `torch`, `faster-whisper`, `pyttsx3` and `pynput` — gigabytes of
wheels, several of which want audio and camera devices a runner does not have.
Installing them would buy a much slower job that still could not exercise the
runtime. `langgraph` and `pytest` are the exception, installed directly in
`ci.yml` rather than through the requirements file: the reasoning graph is
pure Python over those two packages, `core/llm_client.py` imports `ollama`
lazily inside `get_client` specifically so the graph stays importable without
it, and the tests mock `chat` rather than calling a model — so this stays a
seconds-long job with no GPU, camera, or running Ollama instance involved.

**What it does not verify:** the reasoning loop against a real model, speech
recognition, synthesis, and every skill's `execute()`. All of those need a
model, a microphone, or a camera, and none is exercised by anything but
hand-testing and the batch runner in `test_suite.txt`. That is the honest
state of it.
