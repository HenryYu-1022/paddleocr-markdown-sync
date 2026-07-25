import json
import logging
from pathlib import Path

from pypdf import PdfWriter

from paddleocr_markdown_sync.discovery import (
    discover_pdfs,
    document_id,
    fingerprint,
    legacy_document_id,
    plan_daily_work,
)


def write_pdf(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def test_discovery_recurses_all_levels_and_accepts_uppercase(tmp_path):
    write_pdf(tmp_path / "root.pdf")
    write_pdf(tmp_path / "a" / "b" / "deep.PDF")
    (tmp_path / "a" / "notes.txt").write_text("not a PDF", encoding="utf-8")

    found = [path.relative_to(tmp_path).as_posix() for path in discover_pdfs(tmp_path)]

    assert found == ["a/b/deep.PDF", "root.pdf"]


def test_identical_bytes_at_different_paths_are_not_deduplicated(tmp_path):
    first = write_pdf(tmp_path / "first.pdf")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "renamed.pdf").write_bytes(first.read_bytes())

    found = discover_pdfs(tmp_path)

    assert len(found) == 2
    assert document_id(found[0], tmp_path) != document_id(found[1], tmp_path)


def test_daily_plan_stops_before_exceeding_page_limit(tmp_path):
    pdf_root = tmp_path / "pdfs"
    markdown_root = tmp_path / "markdown"
    write_pdf(pdf_root / "a.pdf", 2)
    write_pdf(pdf_root / "b.pdf", 3)

    plan = plan_daily_work(pdf_root, markdown_root, daily_page_limit=4)

    assert [item.source_path.name for item in plan.items] == ["a.pdf"]
    assert plan.planned_pages == 2
    assert plan.deferred_count == 1


def test_completed_matching_fingerprint_is_skipped(tmp_path):
    pdf_root = tmp_path / "pdfs"
    markdown_root = tmp_path / "markdown"
    pdf = write_pdf(pdf_root / "paper.pdf")
    first_plan = plan_daily_work(pdf_root, markdown_root, daily_page_limit=10)
    item = first_plan.items[0]
    item.output_dir.mkdir(parents=True)
    (item.output_dir / "result.jsonl").write_text("{}\n", encoding="utf-8")
    (item.output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "status": "done",
                "source_path": str(pdf),
                "fingerprint": fingerprint(pdf).to_dict(),
            }
        ),
        encoding="utf-8",
    )

    second_plan = plan_daily_work(pdf_root, markdown_root, daily_page_limit=10)

    assert second_plan.items == ()
    assert second_plan.skipped_count == 1


def test_unreadable_pdf_is_deferred_instead_of_bypassing_budget(tmp_path):
    pdf_root = tmp_path / "pdfs"
    markdown_root = tmp_path / "markdown"
    pdf_root.mkdir()
    (pdf_root / "broken.pdf").write_bytes(b"not a pdf")

    plan = plan_daily_work(pdf_root, markdown_root, daily_page_limit=10)

    assert plan.items == ()
    assert plan.unknown_page_count == 1


def test_existing_legacy_done_output_is_reused_without_resubmission(tmp_path):
    pdf_root = tmp_path / "pdfs"
    markdown_root = tmp_path / "markdown"
    pdf = write_pdf(pdf_root / "paper.pdf")
    output = markdown_root / legacy_document_id(pdf)
    output.mkdir(parents=True)
    (output / "result.jsonl").write_text("{}\n", encoding="utf-8")
    (output / "metadata.json").write_text(
        json.dumps(
            {
                "status": "done",
                "source_path": str(pdf),
                "completed_at": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    plan = plan_daily_work(pdf_root, markdown_root, daily_page_limit=10)

    assert plan.items == ()
    assert plan.skipped_count == 1


def test_page_estimation_suppresses_recoverable_pypdf_warnings(
    monkeypatch, caplog, tmp_path
):
    import paddleocr_markdown_sync.discovery as discovery_module

    class WarningReader:
        def __init__(self, path):
            logging.getLogger("pypdf._reader").warning(
                "Ignoring wrong pointing object"
            )
            self.pages = [object()]

    monkeypatch.setattr(discovery_module, "PdfReader", WarningReader)

    assert discovery_module.estimate_page_count(tmp_path / "paper.pdf") == 1
    assert "Ignoring wrong pointing object" not in caplog.text
