# tests/test_machine_skills.py
"""The 5c skills except run_command, which has its own module.

These act on the machine, so almost none of them can be run for real in a test —
nothing here locks a screen or kills a process. What is tested is the layer that
decides *whether* to: the URL scheme allowlist, the protected-process list, the
refusal to kill the process the assistant is running in, and the fact that power
actions are refused rather than guessed at when the verb is unrecognised.

The one thing deliberately not mocked is that these skills declare themselves
destructive. That flag is what routes them through the confirmation node, and a
skill in this file that lost it would become silently unguarded.
"""
import os

import pytest
from skills.os_control.manage_processes import PROTECTED, ManageProcessesSkill
from skills.os_control.open_url import OpenUrlSkill
from skills.os_control.power_control import PowerControlSkill
from skills.utility.disk_report import DiskReportSkill
from skills.utility.network_status import NetworkStatusSkill

psutil = pytest.importorskip("psutil")


# --- open_url ------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "file:///C:/Users/govin/.ssh/id_rsa",
    "file:///etc/passwd",
    "ms-settings:privacy",
    "shell:startup",
    "javascript:alert(1)",
    "data:text/html,<script>x</script>",
])
def test_only_http_and_https_are_opened(url, monkeypatch):
    """A file:// or shell: URL would read local files or launch an application."""
    opened = []

    def fake_open(target):
        opened.append(target)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    result = OpenUrlSkill().execute({"url": url})

    assert result["status"] == "error"
    assert opened == [], f"a refused scheme still reached the browser: {opened}"


def test_a_bare_domain_becomes_https(monkeypatch):
    opened = []

    def fake_open(target):
        opened.append(target)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    result = OpenUrlSkill().execute({"url": "example.com/page"})

    assert result["status"] == "success"
    assert opened == ["https://example.com/page"]


def test_a_slashless_scheme_is_never_rewritten_into_an_https_url(monkeypatch):
    """Regression: 'ms-settings:privacy' has no '//', so a naive check treated it
    as scheme-less and prepended https, producing a URL that passed inspection."""
    opened = []

    def fake_open(target):
        opened.append(target)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    result = OpenUrlSkill().execute({"url": "ms-settings:privacy"})

    assert result["status"] == "error"
    assert "ms-settings:" in result["message"]
    assert opened == []


def test_an_empty_url_is_refused(monkeypatch):
    monkeypatch.setattr("webbrowser.open", lambda target: True)

    assert OpenUrlSkill().execute({})["status"] == "error"


def test_open_url_is_not_marked_destructive():
    """It opens a window; it does not write, delete or synthesise input."""
    assert OpenUrlSkill().manifest.get("destructive", False) is False


# --- manage_processes ----------------------------------------------------


def test_listing_processes_reports_a_count():
    result = ManageProcessesSkill().execute({"action": "list"})

    assert result["status"] == "success"
    assert result["data"]["count"] > 0


def test_listing_can_be_filtered_by_name():
    result = ManageProcessesSkill().execute({"action": "list", "name": "python"})

    assert result["status"] == "success"
    assert result["data"]["count"] >= 1


@pytest.mark.parametrize("name", ["lsass", "csrss.exe", "winlogon", "System", "SVCHOST.EXE"])
def test_a_critical_system_process_is_refused_before_the_gate(name):
    """Confirmation is a human check, and a human may say yes to a name they
    do not recognise as load-bearing. So this is refused in code."""
    result = ManageProcessesSkill().execute({"action": "kill", "name": name})

    assert result["status"] == "error"
    assert "critical system process" in result["message"]


def test_every_protected_name_is_lowercase_and_extensionless():
    """The comparison strips .exe and lowercases, so an entry with either would
    never match anything and would be a silent hole in the list."""
    for name in PROTECTED:
        assert name == name.lower()
        assert not name.endswith(".exe")


def test_it_refuses_to_kill_the_process_it_is_running_in(monkeypatch):
    """Otherwise "close python" takes the assistant down mid-sentence.

    terminate() is intercepted rather than allowed to run, for two reasons. The
    suite must not depend on this process being the only python on the machine —
    it is not, and an earlier test in another module leaves a short-lived one, which
    made this fail only when the whole suite ran. And a test that really does kill
    every process sharing its name would kill an unrelated interpreter the
    developer happened to have open.
    """
    own_name = psutil.Process(os.getpid()).name()
    own_pid = os.getpid()
    terminated = []

    def spy(self):
        terminated.append(self.pid)          # recorded, never carried out

    monkeypatch.setattr(psutil.Process, "terminate", spy)
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], list(procs)))

    result = ManageProcessesSkill().execute({"action": "kill", "name": own_name})

    assert own_pid not in terminated, "it tried to terminate the process it is running in"
    assert psutil.pid_exists(own_pid)
    # Either it was the only match and refused outright, or it skipped itself and
    # said so while dealing with the others.
    assert ("running in" in result["message"]
            or "No running process" in result["message"]
            or "left my own process alone" in result["message"])


def test_it_never_terminates_its_own_parent_either(monkeypatch):
    """The backend is spawned by the Electron main process; killing the parent
    would take the window down with it."""
    terminated = []
    monkeypatch.setattr(psutil.Process, "terminate", lambda self: terminated.append(self.pid))
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], list(procs)))
    parent_pids = {parent.pid for parent in psutil.Process(os.getpid()).parents()}

    for name in {psutil.Process(pid).name() for pid in parent_pids
                 if psutil.pid_exists(pid)} or {"explorer.exe"}:
        ManageProcessesSkill().execute({"action": "kill", "name": name})

    assert not (parent_pids & set(terminated)), "it tried to terminate one of its own parents"


def test_killing_something_that_does_not_exist_says_so():
    result = ManageProcessesSkill().execute(
        {"action": "kill", "name": "a-process-that-is-not-running-xyz"}
    )

    assert result["status"] == "error"
    assert "No running process" in result["message"]


def test_kill_with_no_name_is_refused():
    assert ManageProcessesSkill().execute({"action": "kill"})["status"] == "error"


def test_manage_processes_is_destructive():
    assert ManageProcessesSkill().manifest["destructive"] is True


# --- power_control -------------------------------------------------------


@pytest.mark.parametrize("action", ["", "explode", "hibernate now", "turn off the lights"])
def test_an_unrecognised_power_action_is_refused_without_running_anything(action, monkeypatch):
    ran = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a) or None)

    result = PowerControlSkill().execute({"action": action})

    assert result["status"] == "error"
    assert ran == []


def test_power_control_is_destructive():
    assert PowerControlSkill().manifest["destructive"] is True


def test_the_shutdown_command_carries_an_abortable_delay(monkeypatch):
    """A confirmation given too fast must still be recoverable."""
    from skills.os_control import power_control

    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        # Not `captured.setdefault(...) or _Result()`: setdefault returns the
        # list, which is truthy, so `or` short-circuits and the caller gets a
        # list instead of a result object.
        captured["command"] = command
        return _Result()

    monkeypatch.setattr(power_control, "IS_WINDOWS", True)
    monkeypatch.setattr("subprocess.run", fake_run)

    result = PowerControlSkill().execute({"action": "shutdown"})

    assert result["status"] == "success"
    assert "/t" in captured["command"]
    delay = captured["command"][captured["command"].index("/t") + 1]
    assert int(delay) >= 10
    assert "cancel" in result["message"].lower()


def test_lock_does_not_go_through_the_shutdown_binary(monkeypatch):
    """shutdown.exe cannot lock a workstation; using it would silently do nothing."""
    from skills.os_control import power_control

    captured = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        # Not `captured.setdefault(...) or _Result()`: setdefault returns the
        # list, which is truthy, so `or` short-circuits and the caller gets a
        # list instead of a result object.
        captured["command"] = command
        return _Result()

    monkeypatch.setattr(power_control, "IS_WINDOWS", True)
    monkeypatch.setattr("subprocess.run", fake_run)

    PowerControlSkill().execute({"action": "lock"})

    assert "shutdown" not in captured["command"][0].lower()
    assert "LockWorkStation" in " ".join(captured["command"])


# --- disk_report and network_status --------------------------------------


def test_disk_report_lists_drives():
    result = DiskReportSkill().execute()

    assert result["status"] == "success"
    assert "free of" in result["message"]


def test_disk_report_refuses_a_breakdown_outside_the_workspace(tmp_path, monkeypatch):
    import copy

    from core.config import SETTINGS

    patched = copy.deepcopy(SETTINGS["filesystem"])
    patched["allowed_roots"] = [str(tmp_path / "workspace")]
    (tmp_path / "workspace").mkdir()
    monkeypatch.setitem(SETTINGS, "filesystem", patched)

    result = DiskReportSkill().execute({"path": str(tmp_path)})

    assert result["status"] == "error"
    assert "refused" in result["message"]


def test_disk_report_is_terminal():
    assert DiskReportSkill().manifest["terminal"] is True


def test_network_status_reports_the_local_machine():
    result = NetworkStatusSkill().execute()

    assert result["status"] == "success"
    assert "This machine is" in result["message"]


def test_network_status_reports_an_unresolvable_host_without_raising():
    result = NetworkStatusSkill().execute({"host": "this-host-does-not-exist.invalid"})

    assert result["status"] == "success"
    assert "does not resolve" in result["message"]
