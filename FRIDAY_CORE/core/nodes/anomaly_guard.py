# core/nodes/anomaly_guard.py
"""The anomaly rule, enforced in Python.

Two conditions, joined by OR: more than one person in frame, or the laptop
absent from detections. Either one is an anomaly. The state is latched — it is
held until a scan reports exactly one person and the laptop is back, which is
the hysteresis the rule has always specified.

Detecting the anomaly and *acting* on it are two separate decisions. The rule
used to mute system audio every time, which is a real intervention nobody asked
for; ``privacy.auto_mute`` now gates it and defaults to False. What survives by
default is the announcement, which is the part that was actually wanted: it says
what it saw and leaves the machine alone.

The detection half stays where it was, in Python rather than in the system
prompt, so it fires every time instead of when the model feels like it, and it
is testable against a dict of counts with no model, camera or microphone in the
loop.
"""
from core.config import SETTINGS
from core.state import AgentState


def _is_anomalous(detections: dict[str, int]) -> bool:
    return detections.get("person", 0) > 1 or detections.get("laptop", 0) < 1


def _is_clear(detections: dict[str, int]) -> bool:
    return detections.get("person", 0) == 1 and detections.get("laptop", 0) >= 1


def _set_mute(active_skills: dict, action: str) -> tuple[bool, str]:
    """Ask media_control to change the audio state.

    Returns ``(applied, message)``. The flag matters: the narration used to
    claim "Audio muted" whether or not the skill was loaded or the call
    succeeded, which made FRIDAY report an intervention that never happened.
    """
    skill = active_skills.get("media_control")
    if skill is None:
        return False, "media_control is not loaded, so I could not change the audio state."
    try:
        result = skill.execute({"action": action}) or {}
    except Exception as error:                          # noqa: BLE001
        return False, f"Could not {action}: {error}"
    message = result.get("message", action)
    return result.get("status") == "success", message


def anomaly_guard_node(state: AgentState, active_skills: dict, settings: dict | None = None) -> dict:
    settings = settings or SETTINGS
    privacy = settings.get("privacy", {})
    auto_mute = privacy.get("auto_mute", False)
    announce_only = privacy.get("announce_only", True)

    detections = state.get("detections") or {}
    latched = state.get("anomaly_active", False)

    # Nothing to announce and nothing to change: the guard is switched off.
    if not auto_mute and not announce_only:
        return {}

    if not latched and _is_anomalous(detections):
        note = ("Privacy anomaly detected — more than one person present or the "
                "workstation is out of frame.")
        if auto_mute:
            applied, message = _set_mute(active_skills, "mute")
            note += (" Audio muted until the area is clear." if applied
                     else f" I did not mute the audio: {message}")
    elif latched and _is_clear(detections):
        note = "Area clear, single operator confirmed."
        if auto_mute:
            applied, message = _set_mute(active_skills, "unmute")
            note += " Audio restored." if applied else f" I could not restore the audio: {message}"
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
