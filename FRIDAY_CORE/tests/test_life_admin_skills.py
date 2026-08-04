# tests/test_life_admin_skills.py
"""The 5e skills: task_list, journal, calendar, translate, check_email, track_price.

Two things get real scrutiny. The stores, because a to-do list that loses items or a
journal that files an entry under the wrong day is worse than not having one — and
because task_list and track_price share a file, which is a convention that has to be
enforced rather than trusted. And the boundaries: check_email must never read a body
or accept a password from config, and translate must not claim to have translated
something the model returned nothing for.
"""
import copy
import os
from datetime import datetime, timedelta, timezone

import pytest
from core import notes_store
from core.config import SETTINGS
from skills.utility.calendar_events import CalendarSkill
from skills.utility.check_email import CheckEmailSkill
from skills.utility.journal import JournalSkill
from skills.utility.task_list import TaskListSkill
from skills.utility.translate import TranslateSkill
from skills.web.track_price import TAG, TrackPriceSkill


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    """Point the JSON stores at tmp_path so no test touches the real ones."""
    monkeypatch.setattr("core.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("core.notes_store.CONFIG_DIR", tmp_path)
    return tmp_path


# --- task_list -----------------------------------------------------------


def test_a_task_is_added_and_listed():
    skill = TaskListSkill()
    added = skill.execute({"action": "add", "text": "call the bank"})
    listed = skill.execute({"action": "list"})

    assert added["status"] == "success"
    assert listed["data"]["outstanding"] == 1
    assert "call the bank" in listed["message"]


def test_a_task_survives_being_reloaded_from_disk():
    """The store is the point: an in-memory list would pass a weaker test."""
    TaskListSkill().execute({"action": "add", "text": "renew the passport"})

    assert any("passport" in str(r.get("text")) for r in notes_store.load("tasks"))


def test_marking_a_task_done_removes_it_from_outstanding():
    skill = TaskListSkill()
    added = skill.execute({"action": "add", "text": "book the flight"})
    done = skill.execute({"action": "done", "id": added["data"]["id"]})
    listed = skill.execute({"action": "list"})

    assert done["status"] == "success"
    assert listed["data"]["outstanding"] == 0
    assert listed["data"]["done"] == 1


def test_a_task_can_be_completed_by_its_words_when_unambiguous():
    """The model repeats the task text far more often than it repeats an id."""
    skill = TaskListSkill()
    skill.execute({"action": "add", "text": "water the plants"})

    done = skill.execute({"action": "done", "text": "water the plants"})

    assert done["status"] == "success"


def test_an_ambiguous_text_match_is_refused_rather_than_guessed():
    skill = TaskListSkill()
    skill.execute({"action": "add", "text": "email Sam about the invoice"})
    skill.execute({"action": "add", "text": "email Sam about the contract"})

    done = skill.execute({"action": "done", "text": "email Sam"})

    assert done["status"] == "error"
    assert "one clear task" in done["message"]


def test_an_empty_task_is_refused():
    assert TaskListSkill().execute({"action": "add", "text": "  "})["status"] == "error"


def test_removing_an_unknown_task_says_so():
    assert TaskListSkill().execute({"action": "remove", "id": "nope"})["status"] == "error"


# --- journal -------------------------------------------------------------


def test_a_journal_entry_is_stored_verbatim():
    """No summarising on write: a paraphrased journal is a record of nothing."""
    text = "Shipped the auto-mute switch and broke all four gates on purpose."
    JournalSkill().execute({"action": "add", "text": text})

    stored = notes_store.load("journal")
    assert stored[0]["text"] == text


def test_the_journal_reads_back_todays_entries():
    JournalSkill().execute({"action": "add", "text": "wrote the reading skills"})

    result = JournalSkill().execute({"action": "read", "when": "today"})

    assert result["data"]["entries"] == 1
    assert "wrote the reading skills" in result["message"]


def test_a_day_with_no_entries_says_so_rather_than_inventing_one():
    JournalSkill().execute({"action": "add", "text": "something today"})

    result = JournalSkill().execute({"action": "read", "when": "2020-01-01"})

    assert result["data"]["entries"] == 0
    assert "Nothing is written" in result["message"]


def test_reading_a_week_includes_an_entry_from_three_days_ago():
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    notes_store.add("journal", {"text": "older entry"})
    records = notes_store.load("journal")
    notes_store.update("journal", records[0]["id"], {"created": three_days_ago.isoformat()})

    result = JournalSkill().execute({"action": "read", "when": "week"})

    assert result["data"]["entries"] == 1
    assert "older entry" in result["message"]


def test_an_unparseable_date_says_what_it_assumed():
    JournalSkill().execute({"action": "add", "text": "today's note"})

    result = JournalSkill().execute({"action": "read", "when": "the day before the thing"})

    assert "did not understand" in result["message"]


def test_the_journal_and_the_task_list_do_not_share_records():
    TaskListSkill().execute({"action": "add", "text": "a task"})
    JournalSkill().execute({"action": "add", "text": "a journal entry"})

    assert len(notes_store.load("tasks")) == 1
    assert len(notes_store.load("journal")) == 1


# --- track_price: the shared store convention ----------------------------


def test_a_price_watch_never_shows_up_in_the_task_list():
    """task_list and track_price share tasks.json; the tag is what separates them.
    A shared store with an unenforced convention is a bug waiting for a rename."""
    notes_store.add("tasks", {"tag": TAG, "text": "a watched page", "url": "https://example.com",
                              "observations": []})
    TaskListSkill().execute({"action": "add", "text": "a real task"})

    listed = TaskListSkill().execute({"action": "list"})

    assert listed["data"]["outstanding"] == 1
    assert "watched page" not in listed["message"]


def test_track_price_only_sees_its_own_records():
    TaskListSkill().execute({"action": "add", "text": "not a price watch"})

    result = TrackPriceSkill().execute({"action": "list"})

    assert result["data"]["watches"] == 0


def test_track_price_refuses_a_non_http_url():
    result = TrackPriceSkill().execute({"action": "watch", "url": "file:///etc/passwd"})

    assert result["status"] == "error"


def test_track_price_is_disabled_by_default():
    """The plan defers it until the scheduler has proven itself over weeks."""
    from core.config import DEFAULTS

    assert "track_price" in DEFAULTS["skills"]["disabled"]


@pytest.mark.parametrize("text, expected", [
    ("Now only $1,299.00 while stocks last", "$1,299.00"),
    ("Price: 499 INR including delivery", "499 INR"),
    ("Reduced to £45.50 today", "£45.50"),
    ("costs €19 in store", "€19"),
])
def test_the_price_pattern_finds_a_currency_figure(text, expected):
    from skills.web.track_price import PRICE

    match = PRICE.search(text)
    assert match is not None
    assert match.group(0).strip() == expected


def test_a_page_with_no_price_reports_that_rather_than_a_wrong_number():
    from skills.web.track_price import PRICE

    assert PRICE.search("This page has no prices, only words and 2026 and 12:30.") is None


# --- calendar ------------------------------------------------------------


def _ics(path, body):
    path.write_text(
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//\n" + body + "END:VCALENDAR\n",
        encoding="utf-8",
    )
    return path


def test_no_configured_calendar_says_what_to_configure(monkeypatch):
    monkeypatch.setitem(SETTINGS, "calendar", {"ics_paths": [], "days_ahead": 7})

    result = CalendarSkill().execute({})

    assert result["status"] == "error"
    assert "calendar.ics_paths" in result["message"]


def test_an_event_today_is_reported(tmp_path, monkeypatch):
    pytest.importorskip("icalendar")
    when = datetime.now().astimezone().replace(hour=14, minute=30, second=0, microsecond=0)
    _ics(tmp_path / "cal.ics",
         f"BEGIN:VEVENT\nUID:1\nDTSTART:{when.strftime('%Y%m%dT%H%M%S')}\n"
         "SUMMARY:Standup\nLOCATION:Meet\nEND:VEVENT\n")
    monkeypatch.setitem(SETTINGS, "calendar", {"ics_paths": [str(tmp_path)], "days_ahead": 7})

    result = CalendarSkill().execute({"when": "today"})

    assert result["data"]["events"] == 1
    assert "Standup" in result["message"]
    assert "Meet" in result["message"]


def test_a_weekly_recurring_event_is_expanded(tmp_path, monkeypatch):
    """A calendar without recurrence is a calendar that misses the standup."""
    pytest.importorskip("icalendar")
    start = (datetime.now().astimezone() - timedelta(days=14)).replace(
        hour=9, minute=0, second=0, microsecond=0)
    _ics(tmp_path / "cal.ics",
         f"BEGIN:VEVENT\nUID:2\nDTSTART:{start.strftime('%Y%m%dT%H%M%S')}\n"
         "RRULE:FREQ=WEEKLY\nSUMMARY:Weekly sync\nEND:VEVENT\n")
    monkeypatch.setitem(SETTINGS, "calendar", {"ics_paths": [str(tmp_path)], "days_ahead": 14})

    result = CalendarSkill().execute({"when": "week"})

    assert result["data"]["events"] >= 1
    assert "Weekly sync" in result["message"]


def test_an_event_outside_the_window_is_not_reported(tmp_path, monkeypatch):
    pytest.importorskip("icalendar")
    when = datetime.now().astimezone() + timedelta(days=60)
    _ics(tmp_path / "cal.ics",
         f"BEGIN:VEVENT\nUID:3\nDTSTART:{when.strftime('%Y%m%dT%H%M%S')}\n"
         "SUMMARY:Far future\nEND:VEVENT\n")
    monkeypatch.setitem(SETTINGS, "calendar", {"ics_paths": [str(tmp_path)], "days_ahead": 7})

    result = CalendarSkill().execute({"when": "today"})

    assert result["data"]["events"] == 0


def test_an_unreadable_calendar_file_is_reported_not_swallowed(tmp_path, monkeypatch):
    pytest.importorskip("icalendar")
    (tmp_path / "broken.ics").write_bytes(b"\x00\x01not a calendar")
    monkeypatch.setitem(SETTINGS, "calendar", {"ics_paths": [str(tmp_path)], "days_ahead": 7})

    result = CalendarSkill().execute({"when": "today"})

    assert "could not be read" in result["message"]


# --- check_email: the boundaries -----------------------------------------


def test_no_configured_account_says_what_to_set(monkeypatch):
    monkeypatch.setitem(SETTINGS, "email", {"imap_host": "", "username": "",
                                            "password_env_var": "FRIDAY_EMAIL_PASSWORD"})

    result = CheckEmailSkill().execute({})

    assert result["status"] == "error"
    assert "email.imap_host" in result["message"]


def test_the_password_is_never_read_from_config(monkeypatch):
    """A password in settings.yaml would be read aloud by manage_settings."""
    monkeypatch.setitem(SETTINGS, "email", {
        "imap_host": "imap.example.com", "imap_port": 993, "username": "someone@example.com",
        "password": "should-not-be-used", "password_env_var": "FRIDAY_EMAIL_PASSWORD_TEST",
    })
    monkeypatch.delenv("FRIDAY_EMAIL_PASSWORD_TEST", raising=False)

    result = CheckEmailSkill().execute({})

    assert result["status"] == "error"
    assert "environment variable is not set" in result["message"]
    assert "should-not-be-used" not in result["message"]


def test_a_missing_environment_variable_names_it(monkeypatch):
    monkeypatch.setitem(SETTINGS, "email", {
        "imap_host": "imap.example.com", "username": "a@b.c",
        "password_env_var": "SOME_SPECIFIC_VARIABLE",
    })
    monkeypatch.delenv("SOME_SPECIFIC_VARIABLE", raising=False)

    result = CheckEmailSkill().execute({})

    assert "SOME_SPECIFIC_VARIABLE" in result["message"]


class _FakeImap:
    """Records what was asked of the server. Enough of IMAP4_SSL to drive _fetch."""

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.selected_readonly = None

    def login(self, username, password):
        self.calls.append(("login", username))
        return "OK", []

    def select(self, mailbox, readonly=False):
        self.selected_readonly = readonly
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        self.calls.append(("search", criteria))
        return "OK", [b"1 2"]

    def fetch(self, message_id, spec):
        self.calls.append(("fetch", spec))
        headers = (b"From: Sam <sam@example.com>\r\nSubject: The invoice\r\n"
                   b"Date: Tue, 4 Aug 2026 09:00:00 +0000\r\n\r\n")
        return "OK", [(b"1 (BODY[HEADER])", headers)]

    def close(self):
        self.calls.append(("close",))

    def logout(self):
        self.calls.append(("logout",))


def test_it_asks_for_headers_only_and_never_the_body(monkeypatch):
    """Fetching a body would put arbitrary email — including whatever a stranger
    chose to send — into a language model's prompt. Asserted against the actual
    IMAP conversation rather than by scanning the source, which trips over the
    comment explaining the rule."""
    fake = _FakeImap()
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setitem(SETTINGS, "email", {
        "imap_host": "imap.example.com", "imap_port": 993, "username": "a@b.c",
        "mailbox": "INBOX", "password_env_var": "FAKE_PASSWORD_VAR",
    })
    monkeypatch.setenv("FAKE_PASSWORD_VAR", "app-specific-password")

    result = CheckEmailSkill().execute({})

    fetch_specs = [call[1] for call in fake.calls if call[0] == "fetch"]
    assert fetch_specs, "no fetch was issued"
    for spec in fetch_specs:
        assert "BODY.PEEK[HEADER.FIELDS" in spec
        assert "RFC822" not in spec
        assert "BODY[TEXT]" not in spec
    assert fake.selected_readonly is True
    assert result["status"] == "success"
    assert "The invoice" in result["message"]


def test_the_password_never_appears_in_the_reply(monkeypatch):
    fake = _FakeImap()
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setitem(SETTINGS, "email", {
        "imap_host": "imap.example.com", "imap_port": 993, "username": "a@b.c",
        "mailbox": "INBOX", "password_env_var": "FAKE_PASSWORD_VAR",
    })
    monkeypatch.setenv("FAKE_PASSWORD_VAR", "hunter2-secret")

    result = CheckEmailSkill().execute({})

    assert "hunter2-secret" not in str(result)


def test_unread_count_is_reported_even_when_fewer_are_shown(monkeypatch):
    fake = _FakeImap()
    monkeypatch.setattr("imaplib.IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setitem(SETTINGS, "email", {
        "imap_host": "imap.example.com", "imap_port": 993, "username": "a@b.c",
        "mailbox": "INBOX", "password_env_var": "FAKE_PASSWORD_VAR",
    })
    monkeypatch.setenv("FAKE_PASSWORD_VAR", "x")

    result = CheckEmailSkill().execute({"count": 1})

    assert result["data"]["unread"] == 2
    assert result["data"]["shown"] == 1


def test_mime_encoded_headers_are_decoded():
    decoded = CheckEmailSkill._decode("=?utf-8?B?SGVsbG8gd29ybGQ=?=")

    assert decoded == "Hello world"


def test_the_manifest_does_not_offer_any_write_action():
    manifest = CheckEmailSkill().manifest

    for forbidden in ("send", "reply", "delete", "mark"):
        assert forbidden not in manifest["parameters"]
    assert "cannot send" in manifest["description"]


# --- translate -----------------------------------------------------------


def test_translate_needs_a_target_language():
    assert TranslateSkill().execute({"text": "hello"})["status"] == "error"


def test_translate_needs_text():
    assert TranslateSkill().execute({"to": "French"})["status"] == "error"


def test_translate_returns_the_models_reply_and_names_the_model(monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", lambda *args, **kwargs: "Bonjour le monde")

    result = TranslateSkill().execute({"text": "Hello world", "to": "French"})

    assert result["status"] == "success"
    assert "Bonjour le monde" in result["message"]
    assert SETTINGS["llm"]["model"] in result["message"]


def test_translation_runs_at_zero_temperature(monkeypatch):
    """Creativity here is purely a source of error."""
    captured = {}

    def fake_chat(messages, **kwargs):
        captured.update(kwargs)
        return "translated"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    TranslateSkill().execute({"text": "x", "to": "German"})

    assert captured.get("temperature") == 0.0


def test_an_empty_model_reply_is_an_error_not_a_blank_translation(monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", lambda *args, **kwargs: "   ")

    result = TranslateSkill().execute({"text": "Hello", "to": "Spanish"})

    assert result["status"] == "error"


def test_overlong_input_is_refused_with_the_limit(monkeypatch):
    monkeypatch.setattr("core.llm_client.chat", lambda *args, **kwargs: "should not be called")

    result = TranslateSkill().execute({"text": "x" * 5000, "to": "Italian"})

    assert result["status"] == "error"
    assert "at a time" in result["message"]


def test_environment_is_left_clean():
    """Guards the fixtures above: a leaked env var would make a later run differ."""
    assert "FRIDAY_EMAIL_PASSWORD_TEST" not in os.environ


# --- store durability ----------------------------------------------------


def test_a_corrupt_store_file_does_not_raise(isolated_stores):
    (isolated_stores / "tasks.json").write_text("{not json at all", encoding="utf-8")

    result = TaskListSkill().execute({"action": "list"})

    assert result["status"] == "success"


def test_the_record_limit_is_enforced(monkeypatch):
    monkeypatch.setitem(notes_store.MAX_RECORDS, "tasks", 2)
    skill = TaskListSkill()
    skill.execute({"action": "add", "text": "one"})
    skill.execute({"action": "add", "text": "two"})

    result = skill.execute({"action": "add", "text": "three"})

    assert result["status"] == "error"
    assert "already" in result["message"]


def test_stored_timestamps_are_utc_with_an_offset():
    """A naive local timestamp files a journal entry on the wrong day twice a year."""
    record = notes_store.add("journal", {"text": "x"})

    parsed = datetime.fromisoformat(record["created"])
    assert parsed.tzinfo is not None


def test_local_day_converts_from_utc(monkeypatch):
    assert notes_store.local_day("2026-08-04T10:00:00+00:00") != "unknown"
    assert notes_store.local_day("not a timestamp") == "unknown"


def test_settings_are_not_mutated_by_these_tests():
    """copy.deepcopy in the fixtures, not aliasing, is what makes the suite ordered-safe."""
    assert isinstance(copy.deepcopy(SETTINGS), dict)
