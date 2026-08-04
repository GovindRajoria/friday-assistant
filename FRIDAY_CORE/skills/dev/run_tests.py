# skills/dev/run_tests.py
"""Run a project's test suite and return the failure summary, not the whole log.

This executes code, so it is marked destructive and goes through the confirmation
gate. It is also bounded twice over, and the bounds are the design:

**The runner is chosen from a fixed table, not supplied by the model.** `pytest`
and `npm test` are assembled here as argument lists. The model picks which project
and, at most, which test path — never the program and never the flags. That
distinction is what makes this a different risk from `run_command`, which exists
separately and has to solve the harder problem properly.

**The directory must be in `commands.allowed_roots`,** which is separate from
`projects.allowed_roots` and defaults to empty. Reading a repository and executing
its test suite are different permissions: a suite can drop a database, hit a
network, or take a machine down, and `conftest.py` is arbitrary code that runs on
collection.

**The output is summarised.** A pytest log is thousands of lines and the answer is
the last twenty: what failed and how many. Handing the whole log to a local model
wastes the context window and buries the answer.
"""
import subprocess

from core.config import SETTINGS
from core.project_roots import outside, resolve_in, roots, unconfigured

# name -> (argv, marker file that means "this project uses this runner")
RUNNERS = {
    "pytest": (["-m", "pytest", "-q", "--no-header"], ("pytest.ini", "pyproject.toml", "tests", "setup.cfg")),
    "npm": (["test"], ("package.json",)),
}
MAX_SUMMARY_LINES = 25


class RunTestsSkill:
    def __init__(self):
        self.manifest = {
            "name": "run_tests",
            "description": (
                "Runs the test suite of a project that is explicitly allowed to have commands "
                "run in it, and reports which tests failed and how many passed rather than "
                "the whole log. Parameters: 'path' for the project, optionally 'runner' "
                "(pytest or npm) and 'target' for a single test file. This executes code and "
                "requires confirmation. Use inspect_repo to look at a repository without "
                "running anything."
            ),
            "parameters": ["path", "runner", "target"],
            "destructive": True,
        }

    def execute(self, params=None):
        params = params or {}
        if not roots("commands"):
            return unconfigured("commands", "run a test suite")

        project = resolve_in("commands", params.get("path"))
        if project is None:
            return outside("commands", str(params.get("path")))
        if not project.is_dir():
            return {"status": "error", "message": f"'{project}' is not a directory."}

        runner = str(params.get("runner") or "").lower().strip()
        if not runner:
            runner = self._detect_runner(project)
        if runner not in RUNNERS:
            return {
                "status": "error",
                "message": (f"I do not know how to run '{runner}' tests. I can run: "
                            f"{', '.join(RUNNERS)}."),
            }

        command = self._build(runner, project, params.get("target"))
        if command is None:
            return {
                "status": "error",
                "message": (f"'{project}' does not look like a {runner} project, and the target "
                            "path was outside it."),
            }

        timeout = int(SETTINGS.get("commands", {}).get("timeout_seconds", 120))
        try:
            result = subprocess.run(command, cwd=str(project), capture_output=True,
                                    text=True, timeout=timeout)
        except FileNotFoundError:
            return {"status": "error", "message": f"The {runner} runner is not installed in {project}."}
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "message": (f"The test suite in {project.name} was still running after {timeout}s "
                            "and was stopped. Raise commands.timeout_seconds if that is too short."),
            }

        return self._summarise(runner, project, result)

    # ---- command construction, never from the model -----------------------

    def _build(self, runner, project, target):
        argv, _markers = RUNNERS[runner]
        if runner == "pytest":
            # The interpreter that is already running: the venv's, so the suite
            # sees the same packages the assistant does.
            import sys

            command = [sys.executable, *argv]
        else:
            import shutil

            npm = shutil.which("npm") or shutil.which("npm.cmd")
            if npm is None:
                return None
            command = [npm, *argv]

        if target:
            resolved = resolve_in("commands", str(target))
            if resolved is None or project not in resolved.parents and resolved != project:
                return None
            command.append(str(resolved))
        return command

    @staticmethod
    def _detect_runner(project):
        for name, (_argv, markers) in RUNNERS.items():
            if any((project / marker).exists() for marker in markers):
                return name
        return "pytest"

    # ---- summarising ------------------------------------------------------

    def _summarise(self, runner, project, result):
        combined = f"{result.stdout}\n{result.stderr}".strip()
        lines = [line for line in combined.splitlines() if line.strip()]

        interesting = [line for line in lines
                       if any(token in line for token in
                              ("FAILED", "ERROR", "failed", "passed", "AssertionError",
                               "Tests:", "✕", "✗", "error TS", "not ok"))]
        tail = interesting[-MAX_SUMMARY_LINES:] or lines[-MAX_SUMMARY_LINES:]
        body = "\n".join(f"  {line}" for line in tail) or "  (no output)"

        if result.returncode == 0:
            return {
                "status": "success",
                "message": f"The {runner} suite in {project.name} passed.\n{body}",
                "data": {"passed": True, "exit_code": 0, "runner": runner},
            }
        return {
            "status": "error",
            "message": (f"The {runner} suite in {project.name} failed (exit {result.returncode}). "
                        f"The relevant lines:\n{body}"),
            "data": {"passed": False, "exit_code": result.returncode, "runner": runner},
        }


def setup():
    return RunTestsSkill()
