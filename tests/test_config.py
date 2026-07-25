import os
from pathlib import Path

from paddleocr_markdown_sync.config import (
    config_path,
    credentials_path,
    load_settings,
    read_token,
    write_token,
)


def test_environment_token_overrides_credentials_file(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials.env"
    credentials.write_text("PADDLE_OCR_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("PADDLE_OCR_TOKEN", "environment-token")

    assert read_token(credentials) == "environment-token"


def test_credentials_file_supports_quotes_and_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_TOKEN", raising=False)
    credentials = tmp_path / "credentials.env"
    credentials.write_text(
        "# 本地凭据\nPADDLE_OCR_TOKEN='file-token'\n",
        encoding="utf-8",
    )

    assert read_token(credentials) == "file-token"


def test_load_settings_reads_paths_and_defaults(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[library]\npdf_root = "/papers"\nmarkdown_root = "/markdown"\n',
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.pdf_root == Path("/papers")
    assert settings.markdown_root == Path("/markdown")
    assert settings.daily_page_limit == 19000
    assert settings.poll_interval == 5.0


def test_config_paths_use_override_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path))

    assert config_path() == tmp_path / "config.toml"
    assert credentials_path() == tmp_path / "credentials.env"


def test_windows_config_directory_uses_appdata(tmp_path, monkeypatch):
    import paddleocr_markdown_sync.config as config_module

    monkeypatch.delenv("PADDLEOCR_MD_CONFIG_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(config_module.platform, "system", lambda: "Windows")

    assert config_module.config_dir() == tmp_path / "paddleocr-markdown-sync"


def test_write_token_creates_private_credentials_file(tmp_path, monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_TOKEN", raising=False)
    target = tmp_path / "private" / "credentials.env"

    write_token("secret-value", target)

    assert read_token(target) == "secret-value"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
