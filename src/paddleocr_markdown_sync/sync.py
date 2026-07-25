from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .api import AuthenticationError, PaddleOcrClient, PaddleOcrError
from .discovery import (
    discover_pdfs,
    fingerprint,
    legacy_document_id,
    plan_daily_work,
)
from .exporter import ExportError, materialize_jsonl
from .models import (
    ProcessOutcome,
    Settings,
    SyncSummary,
    WorkItem,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_metadata(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "metadata.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_metadata(output_dir: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json(output_dir / "metadata.json", payload)


def _base_metadata(item: WorkItem, settings: Settings) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "document_id": item.document_id,
        "source_path": str(item.source_path),
        "relative_path": item.relative_path.as_posix(),
        "output_dir": str(item.output_dir),
        "fingerprint": item.fingerprint.to_dict(),
        "page_count_estimate": item.page_count,
        "model": settings.model,
        "status": "pending",
        "job_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def _quota_path(markdown_root: Path) -> Path:
    return markdown_root / ".paddleocr-md" / "quota.json"


def read_daily_usage(
    markdown_root: Path | str,
    *,
    day: str | None = None,
) -> int:
    target_day = day or date.today().isoformat()
    path = _quota_path(Path(markdown_root))
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("date") != target_day:
            return 0
        return max(0, int(payload.get("submitted_pages", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def record_daily_usage(
    markdown_root: Path | str,
    pages: int,
    *,
    day: str | None = None,
) -> int:
    target_day = day or date.today().isoformat()
    root = Path(markdown_root)
    total = read_daily_usage(root, day=target_day) + max(0, int(pages))
    _atomic_write_json(
        _quota_path(root),
        {"date": target_day, "submitted_pages": total, "updated_at": now_iso()},
    )
    return total


def upgrade_legacy_metadata(settings: Settings) -> int:
    input_path = settings.pdf_root.expanduser()
    source_root = input_path.parent if input_path.is_file() else input_path
    upgraded = 0
    for source in discover_pdfs(input_path):
        output_dir = settings.markdown_root / legacy_document_id(source)
        metadata = read_metadata(output_dir)
        if (
            metadata.get("status") != "done"
            or "fingerprint" in metadata
            or not (output_dir / "result.jsonl").is_file()
        ):
            continue
        try:
            stored_source = Path(str(metadata["source_path"])).resolve(strict=False)
            if stored_source != source.resolve(strict=False):
                continue
            relative = source.relative_to(source_root).as_posix()
        except (KeyError, OSError, ValueError):
            continue
        metadata.update(
            {
                "document_id": metadata.get("document_id") or legacy_document_id(source),
                "relative_path": relative,
                "output_dir": str(output_dir),
                "fingerprint": fingerprint(source).to_dict(),
                "updated_at": now_iso(),
            }
        )
        write_metadata(output_dir, metadata)
        upgraded += 1
    return upgraded


def _replace_output(staging: Path, destination: Path) -> None:
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    had_destination = destination.exists()
    if had_destination:
        destination.replace(backup)
    try:
        staging.replace(destination)
    except Exception:
        if had_destination and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def process_item(
    item: WorkItem,
    *,
    client: PaddleOcrClient,
    settings: Settings,
) -> ProcessOutcome:
    metadata = read_metadata(item.output_dir) or _base_metadata(item, settings)
    status = str(metadata.get("status") or "pending")
    job_id = str(metadata.get("job_id") or "").strip()
    resumed = bool(job_id and status in {"submitted", "running"})
    submitted_pages = 0

    try:
        if not resumed:
            job_id = client.submit(item.source_path)
            submitted_pages = item.page_count
            metadata.update(
                {
                    "status": "submitted",
                    "job_id": job_id,
                    "submitted_at": now_iso(),
                    "updated_at": now_iso(),
                    "fingerprint": item.fingerprint.to_dict(),
                    "page_count_estimate": item.page_count,
                    "source_path": str(item.source_path),
                    "relative_path": item.relative_path.as_posix(),
                    "model": settings.model,
                }
            )
            write_metadata(item.output_dir, metadata)
            record_daily_usage(settings.markdown_root, submitted_pages)

        metadata.update({"status": "running", "updated_at": now_iso()})
        write_metadata(item.output_dir, metadata)
        result = client.wait(job_id, poll_interval=settings.poll_interval)
        if result.state == "failed":
            metadata.update(
                {
                    "status": "failed",
                    "error": result.error or "PaddleOCR 任务失败",
                    "updated_at": now_iso(),
                }
            )
            write_metadata(item.output_dir, metadata)
            return ProcessOutcome(
                succeeded=False,
                resumed=resumed,
                submitted_pages=submitted_pages,
                error=str(metadata["error"]),
            )

        if not result.json_url:
            raise ExportError("PaddleOCR 完成结果缺少 JSONL 地址。")
        jsonl_text = client.download_text(result.json_url)
        staging = item.output_dir.with_name(item.output_dir.name + ".staging")
        if staging.exists():
            shutil.rmtree(staging)
        materialized = materialize_jsonl(
            jsonl_text,
            staging,
            downloader=client.download_bytes,
        )
        completed = dict(metadata)
        completed.update(
            {
                "status": "done",
                "jsonl_url": result.json_url,
                "pages_materialized": materialized.pages,
                "markdown_files": [
                    path.relative_to(staging).as_posix()
                    for path in materialized.markdown_files
                ],
                "image_file_count": len(materialized.image_files),
                "completed_at": now_iso(),
                "updated_at": now_iso(),
                "error": None,
            }
        )
        write_metadata(staging, completed)
        _replace_output(staging, item.output_dir)
        return ProcessOutcome(
            succeeded=True,
            resumed=resumed,
            submitted_pages=submitted_pages,
        )
    except AuthenticationError:
        raise
    except (PaddleOcrError, ExportError, OSError, ValueError) as exc:
        metadata.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": now_iso(),
            }
        )
        write_metadata(item.output_dir, metadata)
        return ProcessOutcome(
            succeeded=False,
            resumed=resumed,
            submitted_pages=submitted_pages,
            error=str(metadata["error"]),
        )


def sync_library(
    settings: Settings,
    *,
    client: PaddleOcrClient,
    dry_run: bool = False,
    daily_page_limit: int | None = None,
) -> SyncSummary:
    if not dry_run:
        upgrade_legacy_metadata(settings)
    limit = settings.daily_page_limit if daily_page_limit is None else daily_page_limit
    used_pages = read_daily_usage(settings.markdown_root)
    plan = plan_daily_work(
        settings.pdf_root,
        settings.markdown_root,
        daily_page_limit=limit,
        already_used_pages=used_pages,
    )
    summary = SyncSummary(
        planned=len(plan.items),
        planned_pages=plan.planned_pages,
        skipped=plan.skipped_count,
        deferred=plan.deferred_count,
        unknown_pages=plan.unknown_page_count,
    )
    if dry_run:
        return summary

    settings.markdown_root.mkdir(parents=True, exist_ok=True)
    for item in plan.items:
        outcome = process_item(item, client=client, settings=settings)
        summary = replace(
            summary,
            succeeded=summary.succeeded + int(outcome.succeeded),
            failed=summary.failed + int(not outcome.succeeded),
            resumed=summary.resumed + int(outcome.resumed),
            submitted_pages=summary.submitted_pages + outcome.submitted_pages,
        )
    return summary
