from pathlib import Path


def test_readme_documents_exact_token_destination_and_cross_platform_workflow():
    text = Path("README.md").read_text(encoding="utf-8")

    assert "PADDLE_OCR_TOKEN=" in text
    assert "credentials.env" in text
    assert "paddleocr-md config path" in text
    assert "%APPDATA%" in text
    assert "Application Support" in text
    assert "所有子文件夹" in text
    assert "不进行 PDF 内容去重" in text
    assert "正文和 SI" in text
    assert "PaddleOCR 在线 API" in text
    assert "paddleocr-md schedule install" in text
    assert "page_0001.md" in text


def test_readme_does_not_include_a_real_token():
    text = Path("README.md").read_text(encoding="utf-8")

    token_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("PADDLE_OCR_TOKEN=")
    ]
    assert token_lines
    assert all(
        line in {"PADDLE_OCR_TOKEN=", "PADDLE_OCR_TOKEN=把你的Token粘贴到这里"}
        for line in token_lines
    )
