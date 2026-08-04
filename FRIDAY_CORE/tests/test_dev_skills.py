# tests/test_dev_skills.py
"""The 5b skills. What matters here is the three-way separation of permissions.

`projects.allowed_roots`, `commands.allowed_roots` and `filesystem.allowed_roots`
are deliberately different lists, and the tests that matter are the ones proving
they do not leak into each other: a repository FRIDAY may *read* must not become
one it may *run a test suite in*, and neither may become the workspace it can
delete files from.

Everything else is either bounded by a real git repository built in tmp_path, or
by the fact that these skills report failure reasons rather than raising.
"""
import copy
import subprocess

import pytest
from core.config import SETTINGS
from skills.dev.check_camera_stream import CheckCameraStreamSkill
from skills.dev.gpu_status import GpuStatusSkill
from skills.dev.inspect_repo import InspectRepoSkill
from skills.dev.run_tests import RunTestsSkill
from skills.dev.search_code import SearchCodeSkill


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A real git repository, readable but with no execution permission."""
    repo = tmp_path / "widget"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text(
        "def compute_total(items):\n    return sum(items)\n", encoding="utf-8"
    )
    outside_repo = tmp_path / "other"
    outside_repo.mkdir()
    (outside_repo / "secret.py").write_text("SECRET = 'do not find me'\n", encoding="utf-8")

    for command in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(repo), *command], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e",
                    "commit", "-qm", "first commit"], capture_output=True, check=False)

    monkeypatch.setitem(SETTINGS, "projects", {"allowed_roots": [str(repo)]})
    monkeypatch.setitem(SETTINGS, "commands", copy.deepcopy(
        {"allowed_roots": [], "allowed_executables": [], "timeout_seconds": 30,
         "max_output_chars": 4000}))
    return repo, outside_repo


# --- inspect_repo --------------------------------------------------------


def test_inspect_repo_reports_branch_and_clean_tree(project):
    repo, _ = project

    result = InspectRepoSkill().execute({"path": str(repo)})

    assert result["status"] == "success"
    assert result["data"]["dirty"] == 0
    assert "first commit" in result["message"]


def test_inspect_repo_counts_uncommitted_changes(project):
    repo, _ = project
    (repo / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")

    result = InspectRepoSkill().execute({"path": str(repo), "action": "status"})

    assert result["data"]["dirty"] == 1
    assert "new.py" in result["message"]


def test_inspect_repo_refuses_a_repository_outside_the_project_roots(project):
    _, other = project

    result = InspectRepoSkill().execute({"path": str(other)})

    assert result["status"] == "error"
    assert "refused" in result["message"]


def test_inspect_repo_with_no_roots_configured_says_what_to_configure(monkeypatch):
    monkeypatch.setitem(SETTINGS, "projects", {"allowed_roots": []})

    result = InspectRepoSkill().execute({})

    assert result["status"] == "error"
    assert "projects.allowed_roots" in result["message"]


def test_inspect_repo_rejects_a_directory_that_is_not_a_repository(project, tmp_path, monkeypatch):
    plain = tmp_path / "notarepo"
    plain.mkdir()
    monkeypatch.setitem(SETTINGS, "projects", {"allowed_roots": [str(plain)]})

    result = InspectRepoSkill().execute({"path": str(plain)})

    assert result["status"] == "error"
    assert "not a git repository" in result["message"]


def test_inspect_repo_log_lists_commits(project):
    repo, _ = project

    result = InspectRepoSkill().execute({"path": str(repo), "action": "log"})

    assert result["status"] == "success"
    assert result["data"]["commits"] == 1


# --- search_code ---------------------------------------------------------


def test_search_code_finds_a_definition_with_file_and_line(project):
    repo, _ = project

    result = SearchCodeSkill().execute({"pattern": "compute_total", "path": str(repo)})

    assert result["data"]["matches"] >= 1
    assert "main.py:1" in result["message"]


def test_search_code_can_be_narrowed_by_extension(project):
    repo, _ = project
    (repo / "notes.md").write_text("compute_total is documented here\n", encoding="utf-8")

    result = SearchCodeSkill().execute(
        {"pattern": "compute_total", "path": str(repo), "extension": "md"}
    )

    assert "notes.md" in result["message"]
    assert "main.py" not in result["message"]


def test_search_code_never_leaves_the_project_roots(project):
    """The secret lives outside the configured root and must not be findable."""
    result = SearchCodeSkill().execute({"pattern": "do not find me"})

    assert result["data"]["matches"] == 0


def test_search_code_refuses_an_explicit_outside_path(project):
    _, other = project

    result = SearchCodeSkill().execute({"pattern": "SECRET", "path": str(other)})

    assert result["status"] == "error"


def test_search_code_fallback_scanner_works_without_ripgrep(project):
    """The scan is the path that actually runs on this machine."""
    repo, _ = project

    result = SearchCodeSkill()._scan("compute_total", repo, "")

    assert result["data"]["matches"] == 1
    assert "ripgrep not on PATH" in result["message"]


# --- run_tests: the permission separation --------------------------------


def test_run_tests_refuses_a_project_it_may_only_read(project):
    """The whole point of two lists: readable is not runnable."""
    repo, _ = project

    result = RunTestsSkill().execute({"path": str(repo)})

    assert result["status"] == "error"
    assert "commands" in result["message"]


def test_run_tests_needs_its_own_root_even_when_projects_is_configured(project, monkeypatch):
    repo, _ = project
    assert SETTINGS["projects"]["allowed_roots"] == [str(repo)]

    result = RunTestsSkill().execute({"path": str(repo)})

    assert result["status"] == "error"
    assert "commands.allowed_roots" in result["message"]


def test_run_tests_runs_a_real_suite_when_permitted(project, monkeypatch):
    repo, _ = project
    (repo / "tests").mkdir()
    (repo / "tests" / "test_ok.py").write_text("def test_passes():\n    assert True\n", encoding="utf-8")
    monkeypatch.setitem(SETTINGS, "commands", {"allowed_roots": [str(repo)],
                                               "allowed_executables": [], "timeout_seconds": 60,
                                               "max_output_chars": 4000})

    result = RunTestsSkill().execute({"path": str(repo), "runner": "pytest"})

    assert result["status"] == "success"
    assert result["data"]["passed"] is True


def test_run_tests_reports_a_failure_rather_than_the_whole_log(project, monkeypatch):
    repo, _ = project
    (repo / "tests").mkdir()
    (repo / "tests" / "test_bad.py").write_text(
        "def test_fails():\n    assert 1 == 2\n", encoding="utf-8"
    )
    monkeypatch.setitem(SETTINGS, "commands", {"allowed_roots": [str(repo)],
                                               "allowed_executables": [], "timeout_seconds": 60,
                                               "max_output_chars": 4000})

    result = RunTestsSkill().execute({"path": str(repo), "runner": "pytest"})

    assert result["status"] == "error"
    assert result["data"]["passed"] is False
    assert "test_fails" in result["message"] or "1 failed" in result["message"]


def test_run_tests_is_destructive_so_it_reaches_the_gate():
    assert RunTestsSkill().manifest["destructive"] is True


def test_an_unknown_runner_is_refused(project, monkeypatch):
    repo, _ = project
    monkeypatch.setitem(SETTINGS, "commands", {"allowed_roots": [str(repo)],
                                               "allowed_executables": [], "timeout_seconds": 60,
                                               "max_output_chars": 4000})

    result = RunTestsSkill().execute({"path": str(repo), "runner": "make; rm -rf /"})

    assert result["status"] == "error"
    assert "I can run" in result["message"]


# --- check_camera_stream -------------------------------------------------


@pytest.mark.parametrize("url", ["", "not a url", "ftp://camera/stream", "file:///etc/passwd"])
def test_a_url_that_is_not_a_stream_is_refused(url):
    result = CheckCameraStreamSkill().execute({"url": url})

    assert result["status"] == "error"


def test_credentials_are_stripped_from_the_reported_url():
    """A transcript should not keep a camera password."""
    safe = CheckCameraStreamSkill._safe("rtsp://admin:hunter2@10.0.0.5:554/stream1")

    assert "hunter2" not in safe
    assert "admin" not in safe
    assert "10.0.0.5:554/stream1" in safe


def test_an_unreachable_stream_blames_the_network_not_the_camera():
    """The three failure modes need three different people; do not collapse them."""
    result = CheckCameraStreamSkill().execute({"url": "rtsp://127.0.0.1:1/nonexistent"})

    assert result["status"] == "error"
    assert result["data"]["alive"] is False
    assert result["data"]["stage"] == "connect"
    assert "never reached" in result["message"]


def test_an_unreachable_stream_fails_fast_rather_than_hanging():
    """Regression: OpenCV took 36s to give up on an unroutable address, and its
    own timeout properties did not shorten it on this build. A question like "is
    camera 12 up" is worthless if it takes half a minute, so a TCP pre-flight
    answers first."""
    import time as clock

    started = clock.monotonic()
    CheckCameraStreamSkill().execute({"url": "rtsp://127.0.0.1:1/nonexistent"})

    assert clock.monotonic() - started < 5


def test_a_hostname_that_does_not_resolve_says_so():
    result = CheckCameraStreamSkill().execute(
        {"url": "rtsp://camera-that-does-not-exist.invalid:554/stream"}
    )

    assert result["status"] == "error"
    assert "does not resolve" in result["message"]


# --- gpu_status ----------------------------------------------------------


def test_gpu_status_reports_both_sources():
    """Cannot assert what hardware exists; can assert it answers about both."""
    result = GpuStatusSkill().execute()

    assert result["status"] == "success"
    assert result["data"]["sources"] == 2
    assert "OpenVINO" in result["message"] or "openvino" in result["message"].lower()


def test_gpu_status_is_terminal_so_the_turn_ends_on_its_answer():
    assert GpuStatusSkill().manifest["terminal"] is True
