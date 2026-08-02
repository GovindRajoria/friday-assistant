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


def build_user_message(user_prompt: str, memory_buffer: str = "") -> str:
    """The per-turn user message: who is asking, what was just said, and the question.

    The profile and the recent-context block are not decoration. Replies are
    addressed to a specific operator, and without the transcript of the last
    few turns every question arrives with no history — "what about the other
    one?" has no referent.
    """
    profile = SETTINGS["user"]
    lines = [f"Name: {profile.get('name', 'the operator')}"]
    if profile.get("location"):
        lines.append(f"Location: {profile['location']}")
    if profile.get("interests"):
        lines.append(f"Interests: {profile['interests']}")
    lines.append("Tone: Sophisticated, sharp, and witty.")

    return (
        "User Profile:\n" + "\n".join(lines)
        + f"\n\nRecent Context:\n{memory_buffer}"
        + f"\n\nCurrent Question: {user_prompt}"
    )


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
        1. TERMINATION: When you have enough information to answer, set `action` to "none" and put your complete reply in `final_answer`. "none" is the correct choice for any request that needs no tool — a greeting, a question you can already answer, or a chain that has finished.
        2. NO FILLER ACTIONS: Do not use `core_identity` unless asked "Who are you?".
        3. SINGLE ACTIONS: Execute one tool per thought, but plan the chain in your `thought`.

        AVAILABLE ACTIONS:
        {json.dumps(skill_context, indent=2)}
        """
