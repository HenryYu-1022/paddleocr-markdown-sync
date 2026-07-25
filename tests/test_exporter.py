import json
from pathlib import Path

import pytest

from paddleocr_markdown_sync.exporter import (
    ExportError,
    UnsafeOutputPath,
    materialize_jsonl,
)


def record(markdown_text, images=None, output_images=None):
    return json.dumps(
        {
            "result": {
                "layoutParsingResults": [
                    {
                        "markdown": {
                            "text": markdown_text,
                            "images": images or {},
                        },
                        "outputImages": output_images or {},
                    }
                ]
            }
        }
    )


def test_materialize_writes_numbered_pages_raw_jsonl_and_images(tmp_path):
    downloads = {
        "https://result.test/figure.png": b"figure",
        "https://result.test/page.jpg": b"page",
    }
    text = "\n".join(
        [
            record(
                "# One",
                {"images/figure.png": "https://result.test/figure.png"},
                {"layout": "https://result.test/page.jpg"},
            ),
            record("# Two"),
        ]
    )

    result = materialize_jsonl(text, tmp_path, downloader=downloads.__getitem__)

    assert result.pages == 2
    assert (tmp_path / "page_0001.md").read_text(encoding="utf-8") == "# One"
    assert (tmp_path / "page_0002.md").read_text(encoding="utf-8") == "# Two"
    assert (tmp_path / "result.jsonl").read_text(encoding="utf-8") == text
    assert (tmp_path / "images" / "figure.png").read_bytes() == b"figure"
    assert (tmp_path / "output_images" / "layout_0001.jpg").read_bytes() == b"page"


@pytest.mark.parametrize(
    "unsafe_path",
    ["../secret.png", "/tmp/secret.png", r"C:\secret.png", r"..\secret.png"],
)
def test_materialize_rejects_paths_outside_document_directory(tmp_path, unsafe_path):
    with pytest.raises(UnsafeOutputPath):
        materialize_jsonl(
            record("# One", {unsafe_path: "https://result.test/x"}),
            tmp_path,
            downloader=lambda _: b"x",
        )


def test_materialize_rejects_invalid_jsonl_without_partial_page(tmp_path):
    with pytest.raises(ExportError):
        materialize_jsonl("{not-json}", tmp_path, downloader=lambda _: b"")

    assert not list(tmp_path.glob("page_*.md"))


def test_materialize_rejects_empty_result_instead_of_marking_document_done(tmp_path):
    with pytest.raises(ExportError):
        materialize_jsonl("", tmp_path, downloader=lambda _: b"")

    assert not (tmp_path / "result.jsonl").exists()
