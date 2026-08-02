# core/nodes/anomaly_guard.py
"""The anomaly rule, enforced in Python.

Two conditions, joined by OR: more than one person in frame, or the laptop
absent from detections. Either one mutes the system. The mute is latched — it
is held until a scan reports exactly one person and the laptop is back, which
is the hysteresis the rule has always specified.

This used to be a sentence in the system prompt, which meant it fired when the
model felt like it. Here it fires every time, and it is testable against a
dict of counts with no model, camera or microphone in the loop.
"""
from core.state import AgentState


def _is_anomalous(detections: dict[str, int]) -> bool:
    return detections.get("person", 0) > 1 or detections.get("laptop", 0) < 1


def _is_clear(detections: dict[str, int]) -> bool:
    return detections.get("person", 0) == 1 and detections.get("laptop", 0) >= 1


def _set_mute(active_skills: dict, action: str) -> str:
    skill = active_skills.get("media_control")
    if skill is None:
        return "media_control is not loaded, so I could not change the audio state."
    try:
        return skill.execute({"action": action}).get("message", action)
    except Exception as error:                          # noqa: BLE001
        return f"Could not {action}: {error}"


def anomaly_guard_node(state: AgentState, active_skills: dict) -> dict:
    detections = state.get("detections") or {}
    latched = state.get("anomaly_active", False)

    if not latched and _is_anomalous(detections):
        _set_mute(active_skills, "mute")
        note = ("Privacy anomaly detected — more than one person present or the "
                "workstation is out of frame. Audio muted until the area is clear.")
    elif latched and _is_clear(detections):
        _set_mute(active_skills, "unmute")
        note = "Area clear, single operator confirmed. Audio restored."
        latched = False
    else:
        return {}                                        # nothing changed; stay silent

    if _is_anomalous(detections):
        latched = True
    return {
        "anomaly_active": latched,
        "narration": [note],
        "messages": [*state["messages"], {"role": "user", "content": f"Observation: {note}"}],
    }
