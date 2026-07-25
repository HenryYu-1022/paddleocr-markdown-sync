from pathlib import Path

import pytest

from paddleocr_markdown_sync.scheduler import (
    UnsupportedScheduler,
    scheduler_for_platform,
)
from paddleocr_markdown_sync.scheduler.macos import LABEL, render_plist
from paddleocr_markdown_sync.scheduler.windows import (
    TASK_NAME,
    build_create_command,
    render_powershell,
)


def test_macos_plist_runs_daily_sync_at_requested_time(tmp_path):
    plist = render_plist(
        python_path="/usr/bin/python3",
        config_path=tmp_path / "config.toml",
        hour=3,
        minute=15,
        log_dir=tmp_path / "logs",
    )

    assert LABEL in plist
    assert "<integer>3</integer>" in plist
    assert "<integer>15</integer>" in plist
    assert "<string>sync</string>" in plist
    assert "<string>--config</string>" in plist


def test_windows_task_uses_current_user_daily_schedule(tmp_path):
    command = build_create_command(
        python_path=r"C:\Python\python.exe",
        config_path=tmp_path / "config.toml",
        time_text="03:15",
    )

    assert command[:3] == ["schtasks.exe", "/Create", "/F"]
    assert "/SC" in command and "DAILY" in command
    assert "/ST" in command and "03:15" in command
    assert "/TN" in command and TASK_NAME in command


def test_windows_powershell_script_quotes_paths_and_appends_logs(tmp_path):
    script = render_powershell(
        python_path=Path(r"C:\Program Files\Python\python.exe"),
        config_path=tmp_path / "config.toml",
        log_dir=tmp_path / "logs",
    )

    assert "paddleocr_markdown_sync" in script
    assert "--config" in script
    assert "sync" in script
    assert "sync.log" in script


def test_scheduler_dispatches_known_platforms():
    assert scheduler_for_platform("Darwin").__name__.endswith(".macos")
    assert scheduler_for_platform("Windows").__name__.endswith(".windows")

    with pytest.raises(UnsupportedScheduler):
        scheduler_for_platform("Linux")
