# tests/test_self_knowledge_skills.py
"""The 5d skills: skill_health, explain_last_turn, manage_settings, diagnose_self.

The interesting assertions here are about honesty rather than function. A skill
that reports on the assistant's own state is only worth having if it cannot
report a state that is not real — so these check that a missing turn record says
"no record" rather than inventing one, that a load failure is attributed to the
phase that actually caused it, that a boolean written by voice is stored as a
bool rather than the truthy string "false", and that the settings writer refuses
the keys which would let a conversation talk its way out of the sandbox.
"""
import copy

import pytest
from core import registry, turn_log
from core.config import SETTINGS
from skills.utility.diagnose_self import DiagnoseSelfSkill
from skills.utility.explain_last_turn import ExplainLastTurnSkill
from skills.utility.manage_settings import WRITABLE, ManageSettingsSkill
from skills.utility.skill_health import SkillHealthSkill

# --- skill_health --------------------------------------------------------


@pytest.fixture
def clean_registry_state(monkeypatch):
    monkeypatch.setattr(registry, "LOAD_FAILURES", [])
    monkeypatch.setattr(registry, "SKIPPED_SKILLS", [])
    monkeypatch.setattr(registry, "LOADED_SKILLS", ["one", "two"])


def test_skill_health_reports_a_clean_load(clean_registry_state):
    result = SkillHealthSkill().execute()

    assert result["status"] == "success"
    assert "2 skill(s) loaded" in result["message"]
    assert result["data"]["failures"] == 0


def test_skill_health_separates_import_failures_from_setup_failures(clean_registry_state, monkeypatch):
    """The phase names the fix: a package versus data or hardware."""
    monkeypatch.setattr(registry, "LOAD_FAILURES", [
        {"module": "skills.reading.read_document", "phase": "import",
         "error": "ModuleNotFoundError: No module named 'pypdf'", "detail": ""},
        {"module": "skills.vision.scan_environment", "phase": "setup",
         "error": "FileNotFoundError: yolo11n_openvino_model", "detail": ""},
    ])

    result = SkillHealthSkill().execute()

    assert "could not be imported" in result["message"]
    assert "failed to start" in result["message"]
    assert "pypdf" in result["message"]
    assert "yolo11n_openvino_model" in result["message"]
    assert result["data"]["phases"] == ["import", "setup"]


def test_skill_health_distinguishes_disabled_from_broken(clean_registry_state, monkeypatch):
    monkeypatch.setattr(registry, "SKIPPED_SKILLS",
                        [{"module": "skills.web.track_price", "reason": "disabled in settings"}])

    result = SkillHealthSkill().execute()

    assert "switched off in settings" in result["message"]
    assert result["data"]["disabled"] == 1
    assert result["data"]["failures"] == 0


# --- explain_last_turn ---------------------------------------------------


@pytest.fixture(autouse=True)
def empty_turn_log():
    turn_log.clear()
    yield
    turn_log.clear()


def test_explain_last_turn_admits_it_has_no_record():
    """A restarted backend must say so rather than invent a plausible turn."""
    result = ExplainLastTurnSkill().execute()

    assert result["status"] == "success"
    assert result["data"]["turns"] == 0
    assert "no record" in result["message"]


def test_explain_last_turn_reports_the_tool_and_its_parameters():
    record = turn_log.start("what is the weather in Delhi")
    turn_log.step(record, "thought", text="I should look up the weather")
    turn_log.step(record, "action", name="weather", input={"location": "Delhi"})
    turn_log.step(record, "observation", text="31C and clear")
    turn_log.finish(record, "It is 31 degrees and clear in Delhi.")
    turn_log.start("what did you just do")          # the turn this skill runs inside

    result = ExplainLastTurnSkill().execute()

    assert "what is the weather in Delhi" in result["message"]
    assert "Called weather with location='Delhi'" in result["message"]
    assert "31C and clear" in result["message"]


def test_explain_last_turn_ignores_the_turn_it_is_running_inside():
    record = turn_log.start("earlier question")
    turn_log.finish(record, "earlier answer")
    turn_log.start("what did you just do")

    result = ExplainLastTurnSkill().execute()

    assert "earlier question" in result["message"]
    assert "what did you just do" not in result["message"]


def test_explain_last_turn_reports_a_refusal_with_its_reason():
    record = turn_log.start("delete my documents")
    turn_log.step(record, "action", name="manage_files", input={"action": "delete", "path": "C:/"})
    turn_log.step(record, "confirmation", approved=False,
                  text="The operator declined that action.")
    turn_log.finish(record, "I did not delete anything.")
    turn_log.start("why not")

    result = ExplainLastTurnSkill().execute()

    assert "Refused at the confirmation gate" in result["message"]
    assert "declined" in result["message"]


def test_explain_last_turn_caps_how_far_back_it_looks():
    for index in range(turn_log.MAX_TURNS + 5):
        record = turn_log.start(f"question {index}")
        turn_log.finish(record, f"answer {index}")

    result = ExplainLastTurnSkill().execute({"count": 999})

    assert result["data"]["turns"] <= turn_log.MAX_TURNS


# --- manage_settings -----------------------------------------------------


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """A real settings.yaml with comments, so comment preservation is testable."""
    path = tmp_path / "settings.yaml"
    path.write_text(
        "# Your personal settings.\n\n"
        "privacy:\n"
        "  # The camera anomaly rule still detects and still says so.\n"
        "  auto_mute: false\n"
        "  announce_only: true\n\n"
        "server:\n"
        "  speak: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("skills.utility.manage_settings.SETTINGS_PATH", path)
    live = {"privacy": {"auto_mute": False, "announce_only": True}, "server": {"speak": True},
            "audio": {"speech_rate": 175}}
    for section, values in live.items():
        monkeypatch.setitem(SETTINGS, section, copy.deepcopy(values))
    return path


def test_setting_a_value_writes_it_and_keeps_the_comments(settings_file):
    result = ManageSettingsSkill().execute(
        {"action": "set", "key": "privacy.auto_mute", "value": "yes"}
    )
    text = settings_file.read_text(encoding="utf-8")

    assert result["status"] == "success"
    assert "auto_mute: true" in text
    assert "# The camera anomaly rule still detects" in text     # comment survived
    assert "# Your personal settings." in text


def test_a_write_replaces_the_key_rather_than_duplicating_it(settings_file):
    """Found by breaking the in-place edit on purpose: the fallback path inserted
    a second `auto_mute:` line and left the first, which YAML resolves to the
    last one — so the setting appeared to work while the file quietly grew a
    contradictory duplicate on every change."""
    skill = ManageSettingsSkill()
    skill.execute({"action": "set", "key": "privacy.auto_mute", "value": "yes"})
    skill.execute({"action": "set", "key": "privacy.auto_mute", "value": "no"})
    skill.execute({"action": "set", "key": "privacy.auto_mute", "value": "yes"})
    lines = settings_file.read_text(encoding="utf-8").splitlines()

    occurrences = [line for line in lines if line.strip().startswith("auto_mute:")]
    assert len(occurrences) == 1, f"key written {len(occurrences)} times: {occurrences}"
    assert occurrences[0].strip() == "auto_mute: true"


def test_repeated_writes_do_not_grow_the_file(settings_file):
    skill = ManageSettingsSkill()
    skill.execute({"action": "set", "key": "server.speak", "value": "no"})
    after_one = len(settings_file.read_text(encoding="utf-8").splitlines())
    for _ in range(4):
        skill.execute({"action": "set", "key": "server.speak", "value": "yes"})

    assert len(settings_file.read_text(encoding="utf-8").splitlines()) == after_one


def test_the_written_file_is_still_valid_yaml_that_parses_back(settings_file):
    """The whole point of editing in place is a file that still loads."""
    import yaml

    skill = ManageSettingsSkill()
    skill.execute({"action": "set", "key": "privacy.auto_mute", "value": "yes"})
    skill.execute({"action": "set", "key": "audio.speech_rate", "value": "190"})

    parsed = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert parsed["privacy"]["auto_mute"] is True
    assert parsed["privacy"]["announce_only"] is True
    assert parsed["audio"]["speech_rate"] == 190


def test_a_change_that_needs_no_restart_takes_effect_immediately(settings_file):
    ManageSettingsSkill().execute({"action": "set", "key": "privacy.auto_mute", "value": "on"})

    assert SETTINGS["privacy"]["auto_mute"] is True


def test_a_boolean_is_stored_as_a_bool_not_the_string_false(settings_file):
    """'false' as a string is truthy, and would silently mean the opposite."""
    ManageSettingsSkill().execute({"action": "set", "key": "server.speak", "value": "no"})

    assert SETTINGS["server"]["speak"] is False
    assert isinstance(SETTINGS["server"]["speak"], bool)
    assert "speak: false" in settings_file.read_text(encoding="utf-8")


def test_the_filesystem_allowlist_cannot_be_changed_by_voice(settings_file):
    """Otherwise a conversation could talk its way out of the sandbox."""
    result = ManageSettingsSkill().execute(
        {"action": "set", "key": "filesystem.allowed_roots", "value": "C:/"}
    )

    assert result["status"] == "error"
    assert "not changed in conversation" in result["message"]
    assert "allowed_roots" not in settings_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["llm.host", "commands.allowed_executables", "skills.disabled"])
def test_other_security_relevant_keys_are_read_only(settings_file, key):
    result = ManageSettingsSkill().execute({"action": "set", "key": key, "value": "anything"})

    assert result["status"] == "error"


def test_an_unknown_key_lists_the_real_ones(settings_file):
    result = ManageSettingsSkill().execute(
        {"action": "set", "key": "privacy.do_what_i_mean", "value": "yes"}
    )

    assert result["status"] == "error"
    assert "privacy.auto_mute" in result["message"]


def test_an_invalid_value_is_refused_rather_than_coerced(settings_file):
    before = settings_file.read_text(encoding="utf-8")
    result = ManageSettingsSkill().execute(
        {"action": "set", "key": "proactive.briefing_time", "value": "half past eight"}
    )

    assert result["status"] == "error"
    assert "24-hour time" in result["message"]
    assert settings_file.read_text(encoding="utf-8") == before


def test_a_numeric_range_is_enforced(settings_file):
    result = ManageSettingsSkill().execute(
        {"action": "set", "key": "audio.speech_rate", "value": "9000"}
    )

    assert result["status"] == "error"
    assert "between 80 and 400" in result["message"]


def test_writing_a_key_absent_from_the_file_adds_it_to_its_section(settings_file):
    result = ManageSettingsSkill().execute(
        {"action": "set", "key": "server.speak", "value": "off"}
    )
    ManageSettingsSkill().execute(
        {"action": "set", "key": "audio.speech_rate", "value": "200"}
    )
    text = settings_file.read_text(encoding="utf-8")

    assert result["status"] == "success"
    assert "audio:" in text
    assert "speech_rate: 200" in text


def test_a_bare_leaf_name_resolves_to_its_full_key(settings_file):
    """The model says "auto_mute", not "privacy.auto_mute"."""
    result = ManageSettingsSkill().execute({"action": "get", "key": "auto_mute"})

    assert result["status"] == "success"
    assert result["data"]["key"] == "privacy.auto_mute"


def test_listing_reports_every_writable_key(settings_file):
    result = ManageSettingsSkill().execute({"action": "list"})

    assert result["data"]["writable"] == len(WRITABLE)
    assert "privacy.auto_mute" in result["message"]
    assert "needs a restart" in result["message"]


def test_the_manifest_is_destructive_so_it_reaches_the_confirmation_gate():
    assert ManageSettingsSkill().manifest["destructive"] is True


# --- diagnose_self -------------------------------------------------------


def test_diagnose_self_runs_and_counts_its_checks():
    """Cannot assert a healthy machine; can assert it reports rather than raises."""
    result = DiagnoseSelfSkill().execute()

    assert result["status"] == "success"
    assert result["data"]["checks"] >= 8
    assert isinstance(result["data"]["problems"], int)


def test_diagnose_self_flags_an_unreachable_model_host(monkeypatch):
    monkeypatch.setattr(DiagnoseSelfSkill, "_reachable", staticmethod(lambda host: False))

    result = DiagnoseSelfSkill().execute()

    assert result["data"]["problems"] >= 1
    assert "not answering" in result["message"]


def test_diagnose_self_names_the_pull_command_for_a_missing_model(monkeypatch):
    monkeypatch.setattr(DiagnoseSelfSkill, "_reachable", staticmethod(lambda host: True))
    monkeypatch.setattr(DiagnoseSelfSkill, "_ollama_tags", staticmethod(lambda host: ["other:latest"]))

    result = DiagnoseSelfSkill().execute()

    assert "ollama pull" in result["message"]
