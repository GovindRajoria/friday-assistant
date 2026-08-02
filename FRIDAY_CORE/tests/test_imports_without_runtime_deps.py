# tests/test_imports_without_runtime_deps.py
"""Every module the tests import must import without requirements.txt installed.

CI deliberately does not install requirements.txt — it pulls ultralytics,
torch, faster-whisper, pyttsx3 and pynput, gigabytes of wheels, several of
which want audio and camera devices a runner does not have. It installs only
the handful of pure-Python packages the graph and the server need.

That makes a module-scope `import pyttsx3` (or ollama, or cv2) anywhere in the
import chain a CI-only failure: locally every one of those packages is present,
so the whole suite passes on a developer machine and collapses on the runner.
It happened exactly once, on the first push, when server/app.py imported
core.speaker at module scope for the speech thread.

This runs in a subprocess with those packages masked, because the mask has to
be installed before the first import and cannot be undone cleanly inside the
process running the rest of the suite.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Declared in requirements.txt and genuinely absent on the runner. `requests`
# and `PyYAML` are deliberately NOT in this list even though requirements.txt
# names them: langgraph pulls both in transitively through langchain-core, so
# masking them would test a stricter environment than CI actually has and fail
# on imports that work there.
ABSENT_IN_CI = [
    "ollama", "ultralytics", "cv2", "psutil", "docx", "pyttsx3", "pynput",
    "speech_recognition", "faster_whisper", "pyautogui", "pycaw", "comtypes",
    "bs4",
]

# The modules CI imports: the graph under test, the turn runner, and the
# server the WebSocket tests exercise.
MUST_IMPORT = ["core.graph", "core.session", "core.registry", "server.app"]

PROBE = textwrap.dedent("""
    import sys
    MISSING = set({absent!r})

    class _Mask:
        def find_module(self, name, path=None):
            return self if name.split('.')[0] in MISSING else None

        def load_module(self, name):
            raise ImportError(f"No module named {{name!r}}")

    sys.meta_path.insert(0, _Mask())

    failures = []
    for module in {modules!r}:
        try:
            __import__(module)
        except Exception as error:
            failures.append(f"{{module}}: {{type(error).__name__}}: {{error}}")

    print("\\n".join(failures))
    sys.exit(1 if failures else 0)
""")


def test_modules_import_without_optional_runtime_dependencies():
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(absent=ABSENT_IN_CI, modules=MUST_IMPORT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "these modules need a package CI does not install, so the suite would "
        f"fail to collect on the runner while passing locally:\n{result.stdout}{result.stderr}"
    )
