# skills/utility/manage_settings.py
"""Read and change settings in conversation, writing config/settings.yaml.

The auto-mute complaint that prompted the privacy switch was really a complaint
about reach: the behaviour was changeable, but only by finding a YAML file. "Stop
muting my audio" should be something you can just say.

Three deliberate constraints, because a skill that rewrites its own
configuration is exactly where care belongs.

**An allowlist of keys, not arbitrary YAML.** The model supplies the key, and a
free-form path would let it write `filesystem.allowed_roots` — turning a
conversational convenience into a way to talk its way out of the sandbox. Only
the keys below can be written, and the security-relevant ones are readable but
not writable.

**Values are coerced and validated per key**, so `privacy.auto_mute` cannot
become the string "false", which is truthy and would silently mean the opposite
of what was asked. This is the same class of bug the manifest checker guards
against for `destructive`.

**Comments in settings.yaml survive.** The file is heavily commented and those
comments are load-bearing documentation, so a write edits the one line in place
rather than round-tripping the whole document through a YAML dumper.

Marked destructive: it modifies a file on disk that changes how the assistant
behaves on the next start. That earns a confirmation prompt.
"""
import re

from core.config import CONFIG_DIR, SETTINGS

SETTINGS_PATH = CONFIG_DIR / "settings.yaml"

# key -> (type, human description, requires_restart)
WRITABLE = {
    "privacy.auto_mute": ("bool", "mute system audio when the camera sees a privacy anomaly", False),
    "privacy.announce_only": ("bool", "announce a privacy anomaly without muting", False),
    "server.speak": ("bool", "speak replies out loud", True),
    "screen.enabled": ("bool", "watch the screen continuously", True),
    "vlm.enabled": ("bool", "use the vision model for screen descriptions", True),
    "proactive.enabled": ("bool", "allow reminders and the daily briefing", True),
    "proactive.briefing_enabled": ("bool", "give a daily briefing", True),
    "proactive.briefing_time": ("time", "what time the daily briefing happens", True),
    "proactive.quiet_start": ("time", "when quiet hours begin", True),
    "proactive.quiet_end": ("time", "when quiet hours end", True),
    "audio.speech_rate": ("int:80:400", "how fast the voice speaks, in words per minute", True),
    "audio.stt_enabled": ("bool", "listen through the microphone at all", True),
    "assistant.address_user_as": ("text", "how you are addressed", False),
    "llm.temperature": ("float:0:1", "how deterministic the reasoning is", True),
}

# Readable but never writable from conversation. Widening the filesystem
# allowlist or repointing inference at another host is a decision to make in an
# editor, with the comments in front of you, not one to be talked into.
READ_ONLY = {
    "filesystem.allowed_roots", "llm.host", "llm.model", "vlm.host", "vlm.model",
    "server.host", "server.port", "commands.allowed_executables", "commands.allowed_roots",
    "skills.disabled",
}


class ManageSettingsSkill:
    def __init__(self):
        self.manifest = {
            "name": "manage_settings",
            # The vocabulary here was measured winning routing decisions it should
            # have lost. "whether you mute audio... how fast you speak" took all
            # three volume cases in tools/routing_cases.yaml off media_control,
            # because a phrase in a description competes on its words and not on
            # what the skill is for. Both of those now have a skill of their own,
            # so naming them here is no longer even accurate — the last sentence
            # is the boundary, stated once rather than implied.
            "description": (
                "Reads and changes standing settings written to the configuration file: "
                "whether you mute audio on a privacy anomaly, watch the screen, use the "
                "vision model, give a daily briefing and when, quiet hours, how you address "
                "the user. Parameters: 'action' (list, get, set), 'key' like "
                "'privacy.auto_mute', and 'value'. Use this when asked to change a standing "
                "rule rather than to do something once. For the volume of the speakers use "
                "media_control; for how fast or whether you speak, use voice_control. "
                "Changing a setting requires confirmation."
            ),
            "parameters": ["action", "key", "value"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        action = str(params.get("action") or "list").lower()
        key = self._normalise_key(params.get("key"))

        if action in {"list", "show", "all"}:
            return self._list()
        if action in {"get", "read"}:
            return self._get(key)
        if action in {"set", "change", "write", "update"}:
            return self._set(key, params.get("value"))
        return {"status": "error", "message": f"Unknown settings action '{action}'. Use list, get or set."}

    # ---- actions ----------------------------------------------------------

    def _list(self):
        lines = ["Settings I can change, with their current values:"]
        for key, (_kind, description, restart) in WRITABLE.items():
            note = " (needs a restart)" if restart else ""
            lines.append(f"  {key} = {self._render(self._current(key))} — {description}{note}")
        lines.append("Readable but not changeable by voice: " + ", ".join(sorted(READ_ONLY)))
        return {"status": "success", "message": "\n".join(lines), "data": {"writable": len(WRITABLE)}}

    def _get(self, key):
        if not key:
            return {"status": "error", "message": "Which setting? Ask me to list them if unsure."}
        if key not in WRITABLE and key not in READ_ONLY:
            return self._unknown(key)
        value = self._current(key)
        if value is self._MISSING:
            return {"status": "error", "message": f"'{key}' is not set anywhere I can see."}
        return {
            "status": "success",
            "message": f"{key} is currently {self._render(value)}.",
            "data": {"key": key, "value": value},
        }

    def _set(self, key, raw):
        if not key:
            return {"status": "error", "message": "Which setting should I change?"}
        if key in READ_ONLY:
            return {
                "status": "error",
                "message": (f"'{key}' can be read but not changed in conversation — it controls "
                            "what I am allowed to reach, so it is edited in config/settings.yaml "
                            "deliberately, with the comments in front of you."),
            }
        if key not in WRITABLE:
            return self._unknown(key)

        kind, description, restart = WRITABLE[key]
        coerced, error = self._coerce(kind, raw)
        if error:
            return {"status": "error", "message": f"'{raw}' is not a valid value for {key}: {error}"}

        before = self._current(key)
        try:
            self._write(key, coerced)
        except OSError as exc:
            return {"status": "error", "message": f"Could not write {SETTINGS_PATH}: {exc}"}

        # The live dictionary is updated too, so a setting that does not need a
        # restart takes effect on the next turn rather than only after one.
        self._apply_in_memory(key, coerced)
        tail = (" That needs a restart of the backend before it takes effect."
                if restart else " That takes effect immediately.")
        return {
            "status": "success",
            "message": (f"{key} changed from {self._render(before)} to {self._render(coerced)} "
                        f"({description}).{tail}"),
            "data": {"key": key, "old": before, "new": coerced, "restart_required": restart},
        }

    # ---- reading and writing ---------------------------------------------

    class _MissingType:
        def __repr__(self):
            return "(unset)"

    _MISSING = _MissingType()

    def _current(self, key):
        section, _, leaf = key.partition(".")
        return (SETTINGS.get(section) or {}).get(leaf, self._MISSING)

    def _apply_in_memory(self, key, value):
        section, _, leaf = key.partition(".")
        SETTINGS.setdefault(section, {})[leaf] = value

    def _write(self, key, value):
        """Edit the one line in place, or append a section, preserving comments."""
        section, _, leaf = key.partition(".")
        rendered = self._to_yaml(value)

        text = SETTINGS_PATH.read_text(encoding="utf-8") if SETTINGS_PATH.exists() else ""
        lines = text.splitlines()

        section_at = None
        for index, line in enumerate(lines):
            if re.match(rf"^{re.escape(section)}\s*:\s*$", line):
                section_at = index
                break

        if section_at is None:
            block = "" if not lines or lines[-1].strip() == "" else "\n"
            SETTINGS_PATH.write_text(
                text + f"{block}\n{section}:\n  {leaf}: {rendered}\n", encoding="utf-8"
            )
            return

        # Walk the section's indented body looking for the key.
        index = section_at + 1
        while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
            if re.match(rf"^\s+{re.escape(leaf)}\s*:", lines[index]):
                indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
                lines[index] = f"{indent}{leaf}: {rendered}"
                SETTINGS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
            index += 1

        # Section exists, key does not: insert at the end of the section body.
        insert_at = index
        while insert_at > section_at + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, f"  {leaf}: {rendered}")
        SETTINGS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- value handling ---------------------------------------------------

    @staticmethod
    def _normalise_key(key):
        """'privacy auto mute' and 'auto_mute' both mean privacy.auto_mute."""
        if not key:
            return ""
        text = str(key).strip().lower().replace(" ", "_")
        if text in WRITABLE or text in READ_ONLY:
            return text
        # A bare leaf name is unambiguous for every key in the table but one.
        candidates = [k for k in list(WRITABLE) + sorted(READ_ONLY) if k.split(".")[-1] == text]
        if len(candidates) == 1:
            return candidates[0]
        return text.replace("_", ".", 1) if "." not in text and candidates else text

    @staticmethod
    def _coerce(kind, raw):
        text = str(raw).strip().lower() if raw is not None else ""
        if kind == "bool":
            if text in {"true", "yes", "on", "1", "enable", "enabled"}:
                return True, None
            if text in {"false", "no", "off", "0", "disable", "disabled"}:
                return False, None
            return None, "expected yes or no"
        if kind == "time":
            if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", text):
                hour, minute = text.split(":")
                return f"{int(hour):02d}:{minute}", None
            return None, "expected a 24-hour time like 08:00"
        if kind == "text":
            value = str(raw).strip()
            return (value, None) if value else (None, "expected some text")
        if kind.startswith(("int", "float")):
            parts = kind.split(":")
            caster = int if parts[0] == "int" else float
            try:
                value = caster(text)
            except ValueError:
                return None, f"expected {'a whole number' if caster is int else 'a number'}"
            if len(parts) == 3 and not caster(parts[1]) <= value <= caster(parts[2]):
                return None, f"expected between {parts[1]} and {parts[2]}"
            return value, None
        return None, f"unsupported setting type {kind}"

    @staticmethod
    def _to_yaml(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return f'"{value}"'

    @staticmethod
    def _render(value):
        if value is None:
            return "unset"
        if isinstance(value, bool):
            return "on" if value else "off"
        return repr(value) if isinstance(value, str) else str(value)

    @staticmethod
    def _unknown(key):
        # The nearest match *and* the full list, unlike the spoken skills, which
        # get the suggestion alone. This message goes back to the model as an
        # Observation rather than to a person as speech, and a model that is one
        # character off benefits from the correction while one that guessed
        # wildly benefits from the menu. Reading fourteen dotted key names aloud
        # would be the wrong answer; being handed them is not.
        from core.nearest import did_you_mean

        return {
            "status": "error",
            "message": (f"'{key}' is not a setting I can change."
                        + did_you_mean(key, WRITABLE)
                        + " The ones I can: " + ", ".join(WRITABLE)),
        }


def setup():
    return ManageSettingsSkill()
