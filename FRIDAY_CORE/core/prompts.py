# core/prompts.py
"""The system prompt, extracted from the FridayBrain f-string it used to live in.

The text-protocol sections are gone: THE REACTION LOOP, STRICT RULES FOR TOOL
USE, the PAUSE keyword, the "STRICT TOOL SYNTAX" guardrail about the `Action:`
line, the "Final Answer: [response]" exit condition, and the worked example
that demonstrated all of it. The structured-output schema enforces what that
prose used to ask for, so instructions describing a text format the model can
no longer emit would just be stale noise. The persona, the remaining cognitive
guardrails, the proactive-assistant protocol, and the R&D chain stay — none of
that was about output format.

The anomaly rule used to live here too, as a single sentence with zero
enforcement. It is gone from this file entirely — core/nodes/anomaly_guard.py
enforces it in Python after every scan, which is why it no longer needs asking
for.

One rule had to be *added* rather than carried over. The old protocol ended a
turn with "Final Answer:", and dropping that left nothing describing how to
stop: the schema requires an `action`, so the model picked a tool every turn
and every request ran to the step bound. The TERMINATION rule below names the
"none" sentinel explicitly, because a value the model must choose from an enum
is not discoverable from the enum alone.
"""
import json

from core.config import SETTINGS


def build_user_message(user_prompt: str, memory_buffer: str = "", screen_context: str = "") -> str:
    """The per-turn user message: who is asking, what was just said, and the question.

    The profile and the recent-context block are not decoration. Replies are
    addressed to a specific operator, and without the transcript of the last
    few turns every question arrives with no history — "what about the other
    one?" has no referent.

    `screen_context` is the ambient VLM description (Phase 4), folded in here
    rather than appended to every message in the running transcript. This
    function only runs once per turn — core/nodes/reason.py calls it only
    when building the initial message list, never again while a turn chains
    through further steps — so a stale mid-turn screen description cannot
    accumulate into the model's context on every re-prompt.
    """
    profile = SETTINGS["user"]
    lines = [f"Name: {profile.get('name', 'the operator')}"]
    if profile.get("location"):
        lines.append(f"Location: {profile['location']}")
    if profile.get("interests"):
        lines.append(f"Interests: {profile['interests']}")
    lines.append("Tone: Sophisticated, sharp, and witty.")

    message = (
        "User Profile:\n" + "\n".join(lines)
        + f"\n\nRecent Context:\n{memory_buffer}"
    )
    if screen_context:
        # Ambient and possibly stale by the time the model reads it — worded
        # as a hint, not a fact to act on unprompted.
        message += f"\n\nWhat is currently on screen (ambient, may be stale): {screen_context}"
    message += f"\n\nCurrent Question: {user_prompt}"
    return message


def build_system_prompt(active_skills: dict) -> str:
    settings = SETTINGS
    assistant_name = settings["assistant"]["name"]
    address_as = settings["assistant"]["address_user_as"]

    skill_context = {
        name: {
            "description": skill.manifest["description"],
            "required_parameters": skill.manifest.get("parameters", []),
        }
        for name, skill in active_skills.items()
    }

    return f"""
        You are {assistant_name}, an autonomous engineering partner.
        Your persona is sophisticated, sharp, and proactive. Address the user as '{address_as}'.

        PERMANENT COGNITIVE GUARDRAILS:
        1. INTERNAL CALCULATION: You are a high-speed mathematical processor. Perform all percentages, benchmarks, and arithmetic internally in your `thought`. NEVER call or invent math tools.
        2. DOMAINS OF EXCELLENCE: Your focus is Computer Vision (YOLO/OpenVINO), System Architecture, and Personal AI Evolution. Ignore previous vehicle test data unless explicitly requested.

        THE PROACTIVE ASSISTANT PROTOCOL:
        1. STATE AWARENESS: Begin complex missions with `scan_environment` and `system_check`.
        2. KEEP WATCH: If asked to 'monitor' or 'keep watch', call `scan_environment` sequentially.
        3. RESILIENCE: If a hardware bridge fails, state: 'Hardware conflict in [Module]. Bypassing for current task.'

        RESEARCH & DEVELOPMENT (R&D) CHAIN:
        When tasked with evolution or research:
        1. SEARCH: Find latest benchmarks and documentation.
        2. ANALYZE: Perform internal comparison vs. current stack (e.g., YOLOv11 vs. YOLOv8).
        3. DRAFT: Create a technical briefing using `draft_document`.
        4. LOG: Commit findings to the technical vault using `manage_memory`.

        OPERATING RULES:
        1. GROUND ANYTHING CURRENT: You have no knowledge of today. For news, headlines, current events, weather, prices, scores, or anything that could have changed since you were trained, you MUST call a tool first and answer only from what it returns. Never state a headline, figure or event you did not just read from a tool result. If a tool fails, say the lookup failed — do not fill the gap from memory.
        2. TERMINATION: When you have enough information to answer, set `action` to "none" and put your complete reply in `final_answer`. "none" is the correct choice for any request that needs no tool — a greeting, a question you can already answer from durable knowledge, or a chain that has finished. It is NOT correct for anything covered by rule 1.
        3. ANSWER FROM WHAT YOU FETCHED, AND ONLY THAT: Once a tool has returned what you asked for, use it. Do not repeat the same call, and do not end with "I have noted that" — the user wants the content, so put the actual answer in `final_answer`. Your answer must contain no detail the tool did not report. If a result is thin or vague, say exactly what it returned and that it was all you got; padding it with plausible-sounding specifics is the worst thing you can do, because the user cannot tell which half you made up.
        4. NO FILLER ACTIONS: Do not use `core_identity` unless asked "Who are you?".
        5. SINGLE ACTIONS: Execute one tool per thought, but plan the chain in your `thought`.

        {_about_operator_block()}
        AVAILABLE ACTIONS:
        {json.dumps(skill_context, indent=2)}
        """


def _about_operator_block() -> str:
    """The operator's own biography, or nothing at all when they have not written one.

    In the system prompt rather than the per-turn user message because it is
    durable: who the operator is does not change between turns, and repeating
    it in every message would spend context re-establishing the same facts.
    Returns "" when the file is absent so a fresh checkout gets a prompt with
    no empty heading dangling in it.
    """
    from core.profile import load_profile

    about = load_profile()
    if not about:
        return ""
    return (
        "ABOUT THE OPERATOR — who you are speaking to. Ground your answers in this; "
        "it is durable fact, not something to look up:\n"
        f"{about}\n"
    )
