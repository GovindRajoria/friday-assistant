# tests/test_screen_capture_skills.py
"""screenshot and ocr_screen — the parts that do not need a screen.

Neither skill can be tested end to end here: one needs a display and the other
needs a display plus a Windows OCR language pack, and CI has neither. What *can*
be pinned down is everything that decides what gets captured and where it is
written — the region parser (a model will hand it "100, 100, 800x600" and every
other shape), the monitor index, and the allowlist on the output path.

Both were exercised by hand on the development machine; see the README's known
limitations for what that does and does not establish.
"""
import copy

import pytest
from core.config import SETTINGS
from skills.vision.ocr_screen import OcrScreenSkill
from skills.vision.screenshot import ScreenshotSkill


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    patched = copy.deepcopy(SETTINGS["filesystem"])
    patched["allowed_roots"] = [str(root)]
    monkeypatch.setitem(SETTINGS, "filesystem", patched)
    return root, outside


@pytest.mark.parametrize("skill_class", [ScreenshotSkill, OcrScreenSkill])
def test_region_parsing_accepts_what_a_model_actually_emits(skill_class):
    parse = skill_class._region

    assert parse("0,0,800,600") == {"left": 0, "top": 0, "width": 800, "height": 600}
    assert parse(" 10 , 20 , 30 , 40 ") == {"left": 10, "top": 20, "width": 30, "height": 40}
    assert parse("10;20;30;40") == {"left": 10, "top": 20, "width": 30, "height": 40}
    assert parse("10.0,20.0,30.0,40.0") == {"left": 10, "top": 20, "width": 30, "height": 40}


@pytest.mark.parametrize("skill_class", [ScreenshotSkill, OcrScreenSkill])
@pytest.mark.parametrize("bad", [None, "", "0,0,800", "0,0,800,600,900", "left,top,w,h",
                                 "0,0,0,600", "0,0,800,-1"])
def test_a_region_that_makes_no_sense_falls_back_to_the_whole_screen(skill_class, bad):
    """None means "capture everything", which is the safe reading of a bad region."""
    assert skill_class._region(bad) is None


@pytest.mark.parametrize("skill_class", [ScreenshotSkill, OcrScreenSkill])
def test_monitor_index_defaults_to_the_primary_display(skill_class):
    index = skill_class._monitor_index

    assert index(None, 3) == 1
    assert index("not a number", 3) == 1
    assert index(99, 3) == 1          # out of range falls back rather than raising
    assert index(2, 3) == 2
    assert index(0, 3) == 0           # 0 is mss's all-monitors union, and is allowed


def test_screenshot_refuses_to_write_outside_the_allowlist(workspace):
    _, outside = workspace

    result = ScreenshotSkill().execute({"path": str(outside / "grab.png")})

    assert result["status"] == "error"
    assert "refused" in result["message"]
    assert not (outside / "grab.png").exists()


def test_screenshot_refuses_a_dot_dot_escape(workspace):
    root, outside = workspace

    result = ScreenshotSkill().execute({"path": str(root / ".." / "outside" / "grab.png")})

    assert result["status"] == "error"
    assert not (outside / "grab.png").exists()


def test_screenshot_names_a_file_when_not_given_one(workspace, monkeypatch):
    """No path means a timestamped PNG in the workspace, not a failure."""
    root, _ = workspace
    captured = {}

    def fake_mkdir(*args, **kwargs):
        captured["mkdir"] = True

    # Stop before the capture itself: mss is not available on a headless runner,
    # and what is being checked is the destination, not the pixels.
    monkeypatch.setattr("pathlib.Path.mkdir", fake_mkdir)
    result = ScreenshotSkill().execute({})

    # Either mss is missing (CI) or the capture ran (developer machine). Both are
    # acceptable; what must never happen is a path outside the workspace.
    assert result["status"] in {"success", "error"}
    if result["status"] == "success":
        assert str(root) in result["data"]["path"]
    else:
        assert "refused" not in result["message"]
