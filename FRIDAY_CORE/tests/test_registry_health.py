# tests/test_registry_health.py
"""Discovery has to record why a skill is missing, and honour a disable list.

The old registry printed load failures to stdout and kept nothing, so a skill
that failed to import was indistinguishable from one that had never been
written. Both facts below are what `skill_health` reports, so they are tested
against the real discovery pass over a temporary skills directory rather than
against a mock of it.
"""
import importlib
import sys
import textwrap

import pytest
from core import registry

WORKING = '''
class Fine:
    def __init__(self):
        self.manifest = {"name": "fine_skill", "description": "x" * 50}

    def execute(self, params=None):
        return {"status": "success", "message": "ok"}


def setup():
    return Fine()
'''

BAD_IMPORT = '''
import a_package_that_does_not_exist  # noqa: F401


def setup():
    raise AssertionError("never reached")
'''

BAD_SETUP = '''
class Broken:
    def __init__(self):
        self.manifest = {"name": "broken_skill", "description": "y" * 50}


def setup():
    raise RuntimeError("the model directory is missing")
'''


@pytest.fixture
def skills_tree(tmp_path, monkeypatch):
    """A throwaway skills/ directory, discovered by the real code path.

    The real `skills` package is evicted from sys.modules for the duration and
    restored afterwards. Without that, `import skills.group.fine` resolves
    `skills` to the already-imported real package, whose `__path__` points at
    FRIDAY_CORE/skills, and the temporary modules are simply not found — a
    failure that only appears when another test has imported a skill first,
    which is the worst kind to leave in a suite.
    """
    root = tmp_path / "proj"
    (root / "skills" / "group").mkdir(parents=True)
    (root / "skills" / "__init__.py").write_text("", encoding="utf-8")
    (root / "skills" / "group" / "__init__.py").write_text("", encoding="utf-8")
    for name, body in (("fine", WORKING), ("bad_import", BAD_IMPORT), ("bad_setup", BAD_SETUP)):
        (root / "skills" / "group" / f"{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")

    saved = {name: module for name, module in sys.modules.items()
             if name == "skills" or name.startswith("skills.")}
    for name in saved:
        del sys.modules[name]

    monkeypatch.setattr(registry, "PROJECT_ROOT", root)
    monkeypatch.syspath_prepend(str(root))
    importlib.invalidate_caches()
    try:
        yield root
    finally:
        for name in [n for n in sys.modules if n == "skills" or n.startswith("skills.")]:
            del sys.modules[name]
        sys.modules.update(saved)
        importlib.invalidate_caches()


SETTINGS = {"filesystem": {"allowed_roots": []}, "skills": {"disabled": []}}


def test_a_working_skill_loads(skills_tree):
    skills = registry.discover_skills(SETTINGS)

    assert "fine_skill" in skills


def test_an_import_failure_is_recorded_with_its_reason(skills_tree):
    registry.discover_skills(SETTINGS)

    failures = {f["module"]: f for f in registry.LOAD_FAILURES}
    entry = next(f for name, f in failures.items() if "bad_import" in name)
    assert entry["phase"] == "import"
    assert "a_package_that_does_not_exist" in entry["error"]


def test_a_setup_failure_is_recorded_separately_from_an_import_failure(skills_tree):
    """The distinction matters: one needs a package, the other needs data."""
    registry.discover_skills(SETTINGS)

    entry = next(f for f in registry.LOAD_FAILURES if "bad_setup" in f["module"])
    assert entry["phase"] == "setup"
    assert "model directory is missing" in entry["error"]


def test_a_broken_skill_does_not_stop_the_others_loading(skills_tree):
    skills = registry.discover_skills(SETTINGS)

    assert "fine_skill" in skills
    assert len(registry.LOAD_FAILURES) == 2


def test_disabling_by_manifest_name_removes_the_skill(skills_tree):
    skills = registry.discover_skills({**SETTINGS, "skills": {"disabled": ["fine_skill"]}})

    assert "fine_skill" not in skills
    assert any(s["reason"] == "disabled in settings" for s in registry.SKIPPED_SKILLS)


def test_disabling_by_module_basename_skips_it_before_importing(skills_tree):
    """A heavy skill must be skippable without paying for its imports."""
    skills = registry.discover_skills({**SETTINGS, "skills": {"disabled": ["bad_import"]}})

    assert "fine_skill" in skills
    # It was never imported, so it cannot have failed to import.
    assert not any("bad_import" in f["module"] for f in registry.LOAD_FAILURES)


def test_failures_do_not_accumulate_across_discovery_passes(skills_tree):
    registry.discover_skills(SETTINGS)
    first = len(registry.LOAD_FAILURES)
    registry.discover_skills(SETTINGS)

    assert len(registry.LOAD_FAILURES) == first
