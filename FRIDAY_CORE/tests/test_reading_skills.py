# tests/test_reading_skills.py
"""The 5a reading skills: read_document, read_spreadsheet, search_files.

Two things are worth testing here and one is not. Worth testing: that every one
of them refuses a path outside the allowlist (they take a path straight from the
model, so this is the whole safety story), and that the parsing produces the
right *answer* rather than merely not crashing — a spreadsheet total computed in
Python is the reason these skills exist instead of pasting rows into the prompt.

Not worth faking: pypdf and openpyxl themselves. Real files are written to
tmp_path and read back through the real libraries, because a mocked PdfReader
would test the mock. The PDF is built with pypdf's own writer so no binary
fixture has to live in the repo.
"""
import copy

import pytest
from core.config import SETTINGS
from skills.reading.read_document import ReadDocumentSkill
from skills.reading.read_spreadsheet import ReadSpreadsheetSkill
from skills.reading.search_files import SearchFilesSkill


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An allowlist root, plus a directory outside it holding a secret."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("classified", encoding="utf-8")

    patched = copy.deepcopy(SETTINGS["filesystem"])
    patched["allowed_roots"] = [str(root)]
    monkeypatch.setitem(SETTINGS, "filesystem", patched)
    return root, outside


# --- read_document -------------------------------------------------------


def test_reads_a_markdown_file(workspace):
    root, _ = workspace
    (root / "notes.md").write_text("# Title\n\nBody text here.", encoding="utf-8")

    result = ReadDocumentSkill().execute({"path": str(root / "notes.md")})

    assert result["status"] == "success"
    assert "Body text here." in result["message"]


def test_refuses_a_document_outside_the_allowlist(workspace):
    _, outside = workspace

    result = ReadDocumentSkill().execute({"path": str(outside / "secret.txt")})

    assert result["status"] == "error"
    assert "refused" in result["message"]
    assert "classified" not in result["message"]


def test_refuses_a_document_reached_by_dot_dot(workspace):
    root, _ = workspace

    result = ReadDocumentSkill().execute({"path": str(root / ".." / "outside" / "secret.txt")})

    assert result["status"] == "error"
    assert "classified" not in result["message"]


def test_an_unsupported_extension_says_what_it_handles(workspace):
    root, _ = workspace
    (root / "clip.mp4").write_bytes(b"\x00\x01")

    result = ReadDocumentSkill().execute({"path": str(root / "clip.mp4")})

    assert result["status"] == "error"
    assert ".pdf" in result["message"]


def test_long_text_is_truncated_and_says_so(workspace):
    root, _ = workspace
    (root / "big.txt").write_text("x" * 40000, encoding="utf-8")

    result = ReadDocumentSkill().execute({"path": str(root / "big.txt")})

    assert result["data"]["truncated"] is True
    assert "Truncated" in result["message"]


def test_reads_a_real_pdf(workspace):
    """Built with pypdf's writer so there is no binary fixture in the repo."""
    pypdf = pytest.importorskip("pypdf")
    root, _ = workspace
    path = root / "one.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as handle:
        writer.write(handle)

    result = ReadDocumentSkill().execute({"path": str(path)})

    # A blank page extracts no text, and the honest answer is to say the file
    # opened and had nothing in it rather than to report a failure.
    assert result["status"] == "success"
    assert result["data"]["characters"] == 0
    assert "no extractable text" in result["message"]


def test_a_page_range_is_parsed_and_clamped():
    parse = ReadDocumentSkill._page_range

    assert parse("2-4", 10) == [1, 2, 3]
    assert parse("3", 10) == [2]
    assert parse("8-99", 10) == [7, 8, 9]        # clamped to the document
    assert parse("nonsense", 3) == [0, 1, 2]     # falls back to everything
    assert parse(None, 2) == [0, 1]


# --- read_spreadsheet ----------------------------------------------------


def _csv(root, name="data.csv"):
    (root / name).write_text(
        "item,quantity,price\nbolt,10,2.50\nnut,5,1.25\nwasher,,0.75\n", encoding="utf-8"
    )
    return root / name


def test_spreadsheet_summary_reports_shape(workspace):
    root, _ = workspace

    result = ReadSpreadsheetSkill().execute({"path": str(_csv(root))})

    assert result["status"] == "success"
    assert result["data"]["row_count"] == 3
    assert result["data"]["columns"] == ["item", "quantity", "price"]


def test_column_statistics_are_computed_not_estimated(workspace):
    """The arithmetic is the reason this skill exists rather than pasting rows."""
    root, _ = workspace

    result = ReadSpreadsheetSkill().execute(
        {"path": str(_csv(root)), "action": "column", "column": "price"}
    )

    assert result["status"] == "success"
    assert "sum 4.50" in result["message"]
    assert "min 0.75" in result["message"]
    assert "max 2.50" in result["message"]


def test_blank_cells_are_counted_separately(workspace):
    root, _ = workspace

    result = ReadSpreadsheetSkill().execute(
        {"path": str(_csv(root)), "action": "column", "column": "quantity"}
    )

    assert "2 value(s) present, 1 blank" in result["message"]


def test_find_returns_the_matching_row(workspace):
    root, _ = workspace

    result = ReadSpreadsheetSkill().execute(
        {"path": str(_csv(root)), "action": "find", "column": "item", "value": "nut"}
    )

    assert result["data"]["matches"] == 1
    assert "quantity=5" in result["message"]


def test_a_missing_column_lists_the_real_ones(workspace):
    root, _ = workspace

    result = ReadSpreadsheetSkill().execute(
        {"path": str(_csv(root)), "action": "column", "column": "cost"}
    )

    assert result["status"] == "error"
    assert "item, quantity, price" in result["message"]


def test_spreadsheet_refuses_a_path_outside_the_allowlist(workspace):
    _, outside = workspace
    (outside / "books.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = ReadSpreadsheetSkill().execute({"path": str(outside / "books.csv")})

    assert result["status"] == "error"
    assert "refused" in result["message"]


def test_reads_a_real_xlsx(workspace):
    openpyxl = pytest.importorskip("openpyxl")
    root, _ = workspace
    path = root / "sheet.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["region", "revenue"])
    sheet.append(["north", 100])
    sheet.append(["south", 250])
    book.save(path)

    result = ReadSpreadsheetSkill().execute(
        {"path": str(path), "action": "column", "column": "revenue"}
    )

    assert result["status"] == "success"
    assert "sum 350" in result["message"]


# --- search_files --------------------------------------------------------


def test_search_by_name_finds_a_file(workspace):
    root, _ = workspace
    (root / "invoice-2026.pdf").write_bytes(b"%PDF-1.4")

    result = SearchFilesSkill().execute({"pattern": "invoice", "mode": "name"})

    assert result["data"]["matches"] == 1
    assert "invoice-2026.pdf" in result["message"]


def test_search_by_content_finds_the_line(workspace):
    root, _ = workspace
    (root / "log.txt").write_text("line one\nneedle here\nline three\n", encoding="utf-8")

    result = SearchFilesSkill().execute({"pattern": "needle", "mode": "content"})

    assert result["data"]["matches"] >= 1
    assert "log.txt" in result["message"]


def test_search_never_leaves_the_allowlist(workspace):
    """The secret is outside the root and must not be found by a content search."""
    root, _ = workspace
    (root / "harmless.txt").write_text("nothing to see", encoding="utf-8")

    result = SearchFilesSkill().execute({"pattern": "classified", "mode": "content"})

    assert result["data"]["matches"] == 0


def test_search_refuses_an_explicit_path_outside_the_allowlist(workspace):
    _, outside = workspace

    result = SearchFilesSkill().execute(
        {"pattern": "classified", "mode": "content", "path": str(outside)}
    )

    assert result["status"] == "error"
    assert "refused" in result["message"]


def test_search_with_no_pattern_is_refused(workspace):
    result = SearchFilesSkill().execute({"mode": "name"})

    assert result["status"] == "error"


def test_the_fallback_scanner_agrees_with_ripgrep(workspace):
    """The no-ripgrep path is the reason this keeps working; test it directly."""
    root, _ = workspace
    (root / "notes.txt").write_text("alpha\nbeta needle\n", encoding="utf-8")
    skill = SearchFilesSkill()

    result = skill._scan("needle", [root])

    assert result["data"]["matches"] == 1
    assert "ripgrep not on PATH" in result["message"]
