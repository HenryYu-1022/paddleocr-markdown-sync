from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"


@dataclass(frozen=True)
class Settings:
    pdf_root: Path
    markdown_root: Path
    daily_page_limit: int = 19000
    poll_interval: float = 5.0
    http_timeout: int = 120
    max_retries: int = 3
    job_url: str = DEFAULT_JOB_URL
    model: str = DEFAULT_MODEL


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    mtime_ns: int

    def to_dict(self) -> dict[str, int]:
        return {"size": self.size, "mtime_ns": self.mtime_ns}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "FileFingerprint":
        return cls(size=int(data["size"]), mtime_ns=int(data["mtime_ns"]))


@dataclass(frozen=True)
class WorkItem:
    source_path: Path
    relative_path: Path
    output_dir: Path
    document_id: str
    fingerprint: FileFingerprint
    page_count: int


@dataclass(frozen=True)
class DiscoveryPlan:
    items: tuple[WorkItem, ...]
    planned_pages: int
    skipped_count: int
    deferred_count: int
    unknown_page_count: int


@dataclass(frozen=True)
class JobResult:
    job_id: str
    state: str
    json_url: str | None = None
    error: str | None = None
    extracted_pages: int | None = None


@dataclass(frozen=True)
class MaterializedResult:
    pages: int
    markdown_files: tuple[Path, ...]
    image_files: tuple[Path, ...]


@dataclass(frozen=True)
class ProcessOutcome:
    succeeded: bool
    resumed: bool = False
    submitted_pages: int = 0
    error: str | None = None


@dataclass(frozen=True)
class SyncSummary:
    planned: int
    planned_pages: int
    skipped: int
    deferred: int
    unknown_pages: int
    succeeded: int = 0
    failed: int = 0
    resumed: int = 0
    submitted_pages: int = 0
