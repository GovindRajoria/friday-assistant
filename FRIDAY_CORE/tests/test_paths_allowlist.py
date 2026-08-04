# tests/test_paths_allowlist.py
"""The shared containment rule, tested directly rather than through one skill.

tests/test_manage_files_allowlist.py already exercises this through
manage_files, which is what proved the extraction to core/paths.py was
behaviour-preserving. These test the module every *new* filesystem skill now
depends on, so a regression here is caught once instead of five times.
"""
from pathlib import Path

from core.paths import resolve_within


def test_a_path_inside_a_root_resolves(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    assert resolve_within(str(root / "notes.txt"), [root]) == (root / "notes.txt").resolve()


def test_a_relative_path_is_taken_against_the_first_root(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    assert resolve_within("notes.txt", [root]) == (root / "notes.txt").resolve()


def test_a_path_outside_every_root_is_refused(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()

    assert resolve_within(str(tmp_path / "elsewhere" / "secret.txt"), [root]) is None


def test_dot_dot_cannot_climb_out(tmp_path):
    """Resolution happens before the check, so '..' is defeated by where it lands."""
    root = tmp_path / "workspace"
    root.mkdir()

    assert resolve_within(str(root / ".." / "secret.txt"), [root]) is None


def test_the_root_itself_is_inside_the_root(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    assert resolve_within(str(root), [root]) == root.resolve()


def test_a_sibling_with_the_same_prefix_is_not_inside(tmp_path):
    """String matching would let 'workspace-old' pass as inside 'workspace'."""
    root = tmp_path / "workspace"
    root.mkdir()
    sibling = tmp_path / "workspace-old"
    sibling.mkdir()

    assert resolve_within(str(sibling / "notes.txt"), [root]) is None


def test_no_roots_configured_refuses_everything(tmp_path):
    """An empty allowlist is a closed door, not an open one."""
    assert resolve_within(str(tmp_path / "anything.txt"), []) is None


def test_second_root_is_also_honoured(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()

    assert resolve_within(str(second / "f.txt"), [first, second]) == (second / "f.txt").resolve()


def test_a_nonexistent_path_inside_a_root_still_resolves(tmp_path):
    """Writers need a path that does not exist yet; containment is not existence."""
    root = tmp_path / "workspace"
    root.mkdir()

    resolved = resolve_within(str(root / "new" / "deep" / "file.png"), [root])
    assert resolved is not None
    assert isinstance(resolved, Path)
