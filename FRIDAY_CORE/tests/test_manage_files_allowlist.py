# tests/test_manage_files_allowlist.py
"""The path allowlist under skills/os_control/manage_files.py.

This is the layer beneath the confirmation gate, and it is the one that has
to hold even when a human says yes: confirmation stops a bad request from
being rubber-stamped, the allowlist stops one from being representable. Every
refusal case below therefore asserts on `status == "error"` AND on the target
still existing on disk — a refusal that deleted the file anyway would satisfy
the first assertion alone.

The workspace root is a `tmp_path` rather than the real ~/FridayWorkspace,
patched into SETTINGS. That works because manage_files reads the roots inside
execute() rather than at setup() time; see the comment on _allowed_roots.
"""
import copy
import os

import pytest
from core.config import SETTINGS
from skills.os_control.manage_files import ManageFilesSkill


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A sandboxed allowlist root, plus a sibling directory outside it."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified", encoding="utf-8")

    patched = copy.deepcopy(SETTINGS["filesystem"])
    patched["allowed_roots"] = [str(root)]
    monkeypatch.setitem(SETTINGS, "filesystem", patched)
    return root, outside


def test_listing_and_reading_inside_the_root_succeed(workspace):
    root, _ = workspace
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    skill = ManageFilesSkill()

    listed = skill.execute({"action": "list", "path": str(root)})
    read = skill.execute({"action": "read", "path": str(root / "notes.txt")})

    assert listed["status"] == "success"
    assert "notes.txt" in listed["data"]["entries"]
    assert read["status"] == "success"
    assert read["message"] == "hello"


def test_deleting_inside_the_root_actually_removes_the_file(workspace):
    # The positive case matters as much as the refusals: an allowlist that
    # refuses everything would pass every test below and be useless.
    root, _ = workspace
    doomed = root / "doomed.txt"
    doomed.write_text("goodbye", encoding="utf-8")

    result = ManageFilesSkill().execute({"action": "delete", "path": str(doomed)})

    assert result["status"] == "success"
    assert not doomed.exists()


def test_dot_dot_traversal_out_of_the_root_is_refused(workspace):
    root, outside = workspace
    escape = str(root / ".." / "outside" / "secret.txt")

    result = ManageFilesSkill().execute({"action": "delete", "path": escape})

    assert result["status"] == "error"
    assert "refused" in result["message"]
    assert (outside / "secret.txt").exists()


def test_an_absolute_path_outside_the_root_is_refused(workspace):
    _, outside = workspace
    target = outside / "secret.txt"

    result = ManageFilesSkill().execute({"action": "read", "path": str(target)})

    assert result["status"] == "error"
    assert "refused" in result["message"]
    assert target.exists()


def test_moving_to_a_destination_outside_the_root_is_refused(workspace):
    # The destination needs its own containment check. Checking only the
    # source would let a move exfiltrate a file out of the workspace.
    root, outside = workspace
    source = root / "payload.txt"
    source.write_text("data", encoding="utf-8")

    result = ManageFilesSkill().execute({
        "action": "move",
        "path": str(source),
        "destination": str(outside / "payload.txt"),
    })

    assert result["status"] == "error"
    assert "refused" in result["message"]
    assert source.exists()
    assert not (outside / "payload.txt").exists()


def test_a_symlink_inside_the_root_pointing_outside_it_is_refused(workspace):
    # Resolution happens before the containment check specifically so that
    # the check sees where a link really goes, not where it sits.
    root, outside = workspace
    link = root / "shortcut.txt"
    try:
        os.symlink(outside / "secret.txt", link)
    except (OSError, NotImplementedError, AttributeError) as exc:
        # Symlink creation on Windows needs Developer Mode or admin rights.
        pytest.skip(f"symlinks unavailable on this machine: {exc}")

    result = ManageFilesSkill().execute({"action": "delete", "path": str(link)})

    assert result["status"] == "error"
    assert "refused" in result["message"]
    assert (outside / "secret.txt").exists()


def test_an_unknown_action_is_rejected_before_any_path_work(workspace):
    result = ManageFilesSkill().execute({"action": "chmod", "path": "anything"})

    assert result["status"] == "error"
    assert "Unknown file action" in result["message"]
