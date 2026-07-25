import os
from pathlib import Path

from paddleocr_markdown_sync.cli import build_parser, main
from paddleocr_markdown_sync.config import config_path, credentials_path


def test_config_path_prints_config_and_credentials_locations(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path))

    assert main(["config", "path"]) == 0

    output = capsys.readouterr().out
    assert str(tmp_path / "config.toml") in output
    assert str(tmp_path / "credentials.env") in output


def test_init_writes_config_and_empty_private_credentials(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path / "config"))
    pdf_root = tmp_path / "papers"
    markdown_root = tmp_path / "markdown"
    pdf_root.mkdir()

    assert (
        main(
            [
                "init",
                "--pdf-root",
                str(pdf_root),
                "--markdown-root",
                str(markdown_root),
            ]
        )
        == 0
    )

    assert 'pdf_root = "' in config_path().read_text(encoding="utf-8")
    assert "PADDLE_OCR_TOKEN=" in credentials_path().read_text(encoding="utf-8")
    assert "把 Token" in capsys.readouterr().out
    assert markdown_root.is_dir()
    if os.name != "nt":
        assert credentials_path().stat().st_mode & 0o777 == 0o600


def test_doctor_returns_two_when_token_is_missing(monkeypatch, tmp_path, capsys):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("PADDLE_OCR_TOKEN", raising=False)
    pdf_root = tmp_path / "papers"
    pdf_root.mkdir()
    main(
        [
            "init",
            "--pdf-root",
            str(pdf_root),
            "--markdown-root",
            str(tmp_path / "markdown"),
        ]
    )

    assert main(["doctor"], endpoint_checker=lambda *_: True) == 2

    assert "PADDLE_OCR_TOKEN" in capsys.readouterr().out


def test_doctor_passes_with_valid_local_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PADDLE_OCR_TOKEN", "configured-token")
    pdf_root = tmp_path / "papers"
    pdf_root.mkdir()
    main(
        [
            "init",
            "--pdf-root",
            str(pdf_root),
            "--markdown-root",
            str(tmp_path / "markdown"),
        ]
    )

    assert main(["doctor"], endpoint_checker=lambda *_: True) == 0


def test_sync_dry_run_does_not_require_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("PADDLE_OCR_TOKEN", raising=False)
    pdf_root = tmp_path / "papers"
    pdf_root.mkdir()
    main(
        [
            "init",
            "--pdf-root",
            str(pdf_root),
            "--markdown-root",
            str(tmp_path / "markdown"),
        ]
    )

    assert main(["sync", "--dry-run"]) == 0

    assert "计划转换" in capsys.readouterr().out


def test_schedule_install_command_accepts_daily_time():
    args = build_parser().parse_args(["schedule", "install", "--time", "03:15"])

    assert args.command == "schedule"
    assert args.schedule_command == "install"
    assert args.time == "03:15"
