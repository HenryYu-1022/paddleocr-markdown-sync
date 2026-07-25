import json
from pathlib import Path

from pypdf import PdfWriter
import pytest

from paddleocr_markdown_sync.discovery import fingerprint, plan_daily_work
from paddleocr_markdown_sync.discovery import legacy_document_id
from paddleocr_markdown_sync.models import JobResult, Settings
from paddleocr_markdown_sync.sync import (
    read_daily_usage,
    sync_library,
)


SAMPLE_JSONL = json.dumps(
    {
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {"text": "# Converted", "images": {}},
                    "outputImages": {},
                }
            ]
        }
    }
)


def write_pdf(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


class FakeClient:
    def __init__(self):
        self.submitted = []
        self.waited = []
        self.results = {}

    def submit(self, path):
        self.submitted.append(Path(path))
        return f"job-{len(self.submitted)}"

    def wait(self, job_id, *, poll_interval):
        self.waited.append((job_id, poll_interval))
        return self.results.get(
            job_id,
            JobResult(
                job_id=job_id,
                state="done",
                json_url=f"https://result.test/{job_id}.jsonl",
                extracted_pages=1,
            ),
        )

    def download_text(self, url):
        return SAMPLE_JSONL

    def download_bytes(self, url):
        return b"image"


def settings(tmp_path, *, daily_page_limit=19000):
    return Settings(
        pdf_root=tmp_path / "pdfs",
        markdown_root=tmp_path / "markdown",
        daily_page_limit=daily_page_limit,
        poll_interval=0,
    )


def test_new_pdf_is_submitted_materialized_and_recorded(tmp_path):
    configured = settings(tmp_path)
    pdf = write_pdf(configured.pdf_root / "nested" / "paper.pdf")
    client = FakeClient()

    summary = sync_library(configured, client=client)

    assert summary.succeeded == 1
    assert client.submitted == [pdf]
    plan = plan_daily_work(
        configured.pdf_root,
        configured.markdown_root,
        daily_page_limit=10,
    )
    assert plan.skipped_count == 1
    output_dirs = [path.parent for path in configured.markdown_root.rglob("page_0001.md")]
    assert len(output_dirs) == 1
    metadata = json.loads((output_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "done"
    assert metadata["source_path"] == str(pdf)
    assert read_daily_usage(configured.markdown_root) == 1


def test_submitted_job_is_resumed_without_resubmitting(tmp_path):
    configured = settings(tmp_path)
    pdf = write_pdf(configured.pdf_root / "paper.pdf")
    item = plan_daily_work(
        configured.pdf_root,
        configured.markdown_root,
        daily_page_limit=10,
    ).items[0]
    item.output_dir.mkdir(parents=True)
    (item.output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "document_id": item.document_id,
                "source_path": str(pdf),
                "relative_path": item.relative_path.as_posix(),
                "fingerprint": fingerprint(pdf).to_dict(),
                "page_count_estimate": 1,
                "status": "submitted",
                "job_id": "existing-job",
            }
        ),
        encoding="utf-8",
    )
    client = FakeClient()

    summary = sync_library(configured, client=client)

    assert summary.succeeded == 1
    assert summary.resumed == 1
    assert client.submitted == []
    assert client.waited == [("existing-job", 0)]


def test_failed_remote_job_is_recorded_and_other_files_continue(tmp_path):
    configured = settings(tmp_path)
    write_pdf(configured.pdf_root / "a.pdf")
    write_pdf(configured.pdf_root / "b.pdf")
    client = FakeClient()
    client.results["job-1"] = JobResult(
        job_id="job-1",
        state="failed",
        error="remote parse failure",
    )

    summary = sync_library(configured, client=client)

    assert summary.failed == 1
    assert summary.succeeded == 1
    metadata = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in configured.markdown_root.rglob("metadata.json")
    ]
    assert sorted(item["status"] for item in metadata) == ["done", "failed"]


def test_dry_run_does_not_submit_or_consume_daily_usage(tmp_path):
    configured = settings(tmp_path)
    write_pdf(configured.pdf_root / "paper.pdf", pages=2)
    client = FakeClient()

    summary = sync_library(configured, client=client, dry_run=True)

    assert summary.planned == 1
    assert summary.planned_pages == 2
    assert client.submitted == []
    assert read_daily_usage(configured.markdown_root) == 0


def test_sync_upgrades_legacy_done_metadata_without_resubmitting(tmp_path):
    configured = settings(tmp_path)
    pdf = write_pdf(configured.pdf_root / "paper.pdf")
    output = configured.markdown_root / legacy_document_id(pdf)
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
    client = FakeClient()

    summary = sync_library(configured, client=client)

    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert summary.skipped == 1
    assert client.submitted == []
    assert metadata["fingerprint"] == fingerprint(pdf).to_dict()


def test_submitted_pages_are_recorded_before_polling_can_be_interrupted(tmp_path):
    configured = settings(tmp_path)
    write_pdf(configured.pdf_root / "paper.pdf", pages=2)

    class InterruptedClient(FakeClient):
        def wait(self, job_id, *, poll_interval):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        sync_library(configured, client=InterruptedClient())

    assert read_daily_usage(configured.markdown_root) == 2


def test_existing_submitted_job_resumes_even_when_daily_budget_is_full(tmp_path):
    configured = settings(tmp_path, daily_page_limit=1)
    pdf = write_pdf(configured.pdf_root / "paper.pdf", pages=2)
    item = plan_daily_work(
        configured.pdf_root,
        configured.markdown_root,
        daily_page_limit=10,
    ).items[0]
    item.output_dir.mkdir(parents=True)
    (item.output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "document_id": item.document_id,
                "source_path": str(pdf),
                "relative_path": item.relative_path.as_posix(),
                "fingerprint": fingerprint(pdf).to_dict(),
                "page_count_estimate": 2,
                "status": "submitted",
                "job_id": "existing-job",
            }
        ),
        encoding="utf-8",
    )
    client = FakeClient()

    summary = sync_library(configured, client=client)

    assert summary.succeeded == 1
    assert summary.resumed == 1
    assert summary.planned_pages == 0
    assert client.submitted == []
