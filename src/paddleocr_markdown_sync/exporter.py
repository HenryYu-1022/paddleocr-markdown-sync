from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

import requests

from .discovery import safe_stem
from .models import MaterializedResult


class ExportError(RuntimeError):
    """PaddleOCR output could not be materialized."""


class UnsafeOutputPath(ExportError):
    """An API-provided image path would escape the document directory."""


def safe_relative_path(raw: str) -> Path:
    if not raw or "\x00" in raw:
        raise UnsafeOutputPath(raw)
    posix = PurePosixPath(raw)
    windows = PureWindowsPath(raw)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise UnsafeOutputPath(raw)
    normalized = Path(*posix.parts)
    if not normalized.parts:
        raise UnsafeOutputPath(raw)
    return normalized


def _default_downloader(url: str) -> bytes:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _atomic_write_text(path: Path, content: str) -> None:
    _atomic_write_bytes(path, content.encode("utf-8"))


def materialize_jsonl(
    text: str,
    target: Path | str,
    *,
    downloader: Callable[[str], bytes] = _default_downloader,
) -> MaterializedResult:
    output_dir = Path(target)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_files: list[Path] = []
    image_files: list[Path] = []
    page_number = 0

    try:
        records = [
            json.loads(line)
            for line in text.splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        raise ExportError(f"JSONL 第 {exc.lineno} 行无法解析。") from exc
    if not records:
        raise ExportError("PaddleOCR JSONL 结果为空。")

    try:
        for record in records:
            results = record["result"]["layoutParsingResults"]
            if not isinstance(results, list):
                raise TypeError("layoutParsingResults is not a list")
            for parsed in results:
                page_number += 1
                markdown = parsed["markdown"]
                markdown_text = str(markdown["text"])
                markdown_path = output_dir / f"page_{page_number:04d}.md"
                _atomic_write_text(markdown_path, markdown_text)
                markdown_files.append(markdown_path)

                for raw_path, url in (markdown.get("images") or {}).items():
                    image_path = output_dir / safe_relative_path(str(raw_path))
                    _atomic_write_bytes(image_path, downloader(str(url)))
                    image_files.append(image_path)

                for image_name, url in (parsed.get("outputImages") or {}).items():
                    clean_name = safe_stem(Path(str(image_name)).stem)
                    image_path = (
                        output_dir
                        / "output_images"
                        / f"{clean_name}_{page_number:04d}.jpg"
                    )
                    _atomic_write_bytes(image_path, downloader(str(url)))
                    image_files.append(image_path)
    except UnsafeOutputPath:
        raise
    except (KeyError, TypeError, ValueError, OSError, requests.RequestException) as exc:
        raise ExportError(f"PaddleOCR 结果导出失败：{type(exc).__name__}") from exc

    if page_number == 0:
        raise ExportError("PaddleOCR JSONL 未包含可导出的页面。")
    _atomic_write_text(output_dir / "result.jsonl", text)
    return MaterializedResult(
        pages=page_number,
        markdown_files=tuple(markdown_files),
        image_files=tuple(image_files),
    )
