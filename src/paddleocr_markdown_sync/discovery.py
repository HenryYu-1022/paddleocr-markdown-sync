from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from .models import DiscoveryPlan, FileFingerprint, WorkItem


def discover_pdfs(root: Path | str) -> list[Path]:
    root_path = Path(root).expanduser()
    if root_path.is_file():
        return [root_path] if root_path.suffix.lower() == ".pdf" else []
    if not root_path.is_dir():
        return []
    return sorted(
        (
            path
            for path in root_path.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: path.relative_to(root_path).as_posix().casefold(),
    )


def safe_stem(stem: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._-")
    cleaned = cleaned or "document"
    return cleaned[:80].rstrip("._-") or "document"


def document_id(path: Path | str, root: Path | str) -> str:
    source = Path(path)
    root_path = Path(root)
    relative = source.relative_to(root_path).as_posix()
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
    return f"{safe_stem(source.stem)}-{digest}"


def legacy_document_id(path: Path | str) -> str:
    source = Path(path).expanduser()
    normalized = str(source.resolve(strict=False))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{safe_stem(source.stem)}-{digest}"


def fingerprint(path: Path | str) -> FileFingerprint:
    stat = Path(path).stat()
    return FileFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def estimate_page_count(path: Path | str) -> int | None:
    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level
    pypdf_logger.setLevel(logging.ERROR)
    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return None
    finally:
        pypdf_logger.setLevel(previous_level)


def output_dir_for(
    path: Path,
    pdf_root: Path,
    markdown_root: Path,
) -> tuple[str, Path]:
    new_id = document_id(path, pdf_root)
    new_output = markdown_root / new_id
    legacy_id = legacy_document_id(path)
    legacy_output = markdown_root / legacy_id
    if legacy_output.exists() and not new_output.exists():
        return legacy_id, legacy_output
    return new_id, new_output


def completed_matches(
    output_dir: Path,
    current: FileFingerprint,
    source_path: Path,
) -> bool:
    metadata_path = output_dir / "metadata.json"
    result_path = output_dir / "result.jsonl"
    if not metadata_path.is_file() or not result_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if metadata.get("status") != "done":
        return False
    try:
        stored = FileFingerprint.from_dict(metadata["fingerprint"])
    except (ValueError, KeyError, TypeError):
        stored = None
    if stored is not None:
        return stored == current

    # Compatibility with the original markdown_rag exporter, whose completed
    # metadata predates explicit size/mtime fingerprints. The first real sync
    # upgrades these records with a current fingerprint; until then, trust the
    # exact source path so a migration does not reconsume cloud quota.
    try:
        stored_source = Path(str(metadata["source_path"])).resolve(strict=False)
        return stored_source == source_path.resolve(strict=False)
    except (KeyError, OSError, ValueError, TypeError):
        return False


def resumable_matches(output_dir: Path, current: FileFingerprint) -> bool:
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        stored = FileFingerprint.from_dict(metadata["fingerprint"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return (
        metadata.get("status") in {"submitted", "running"}
        and bool(str(metadata.get("job_id") or "").strip())
        and stored == current
    )


def plan_daily_work(
    pdf_root: Path | str,
    markdown_root: Path | str,
    *,
    daily_page_limit: int,
    already_used_pages: int = 0,
) -> DiscoveryPlan:
    input_path = Path(pdf_root).expanduser()
    single_file = input_path.is_file()
    source_root = input_path.parent if single_file else input_path
    output_root = Path(markdown_root).expanduser()
    items: list[WorkItem] = []
    planned_pages = 0
    skipped_count = 0
    deferred_count = 0
    unknown_page_count = 0

    sources = discover_pdfs(input_path if single_file else source_root)
    for source in sources:
        current_fingerprint = fingerprint(source)
        if single_file:
            doc_id = legacy_document_id(source)
            output_dir = output_root / doc_id
        else:
            doc_id, output_dir = output_dir_for(source, source_root, output_root)
        if completed_matches(output_dir, current_fingerprint, source):
            skipped_count += 1
            continue
        resumable = resumable_matches(output_dir, current_fingerprint)
        page_count = estimate_page_count(source)
        if page_count is None and resumable:
            try:
                page_count = int(
                    json.loads(
                        (output_dir / "metadata.json").read_text(encoding="utf-8")
                    )["page_count_estimate"]
                )
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                page_count = None
        if page_count is None:
            unknown_page_count += 1
            continue
        if resumable:
            items.append(
                WorkItem(
                    source_path=source,
                    relative_path=source.relative_to(source_root),
                    output_dir=output_dir,
                    document_id=doc_id,
                    fingerprint=current_fingerprint,
                    page_count=page_count,
                )
            )
            continue
        if already_used_pages + planned_pages + page_count > daily_page_limit:
            deferred_count += 1
            continue
        items.append(
            WorkItem(
                source_path=source,
                relative_path=source.relative_to(source_root),
                output_dir=output_dir,
                document_id=doc_id,
                fingerprint=current_fingerprint,
                page_count=page_count,
            )
        )
        planned_pages += page_count

    return DiscoveryPlan(
        items=tuple(items),
        planned_pages=planned_pages,
        skipped_count=skipped_count,
        deferred_count=deferred_count,
        unknown_page_count=unknown_page_count,
    )
