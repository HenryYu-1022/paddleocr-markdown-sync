from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import SchedulerError, parse_time_text


TASK_NAME = "PaddleOCR Markdown Sync"


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render_powershell(
    *,
    python_path: Path,
    config_path: Path,
    log_dir: Path,
) -> str:
    log_path = log_dir / "sync.log"
    return (
        "$ErrorActionPreference = 'Stop'\n"
        f"& {_ps_quote(python_path)} -m paddleocr_markdown_sync "
        f"--config {_ps_quote(config_path)} sync *>> {_ps_quote(log_path)}\n"
        "exit $LASTEXITCODE\n"
    )


def build_create_command(
    *,
    python_path: str,
    config_path: Path,
    time_text: str,
    script_path: Path | None = None,
) -> list[str]:
    parse_time_text(time_text)
    if script_path is None:
        task_command = subprocess.list2cmdline(
            [
                python_path,
                "-m",
                "paddleocr_markdown_sync",
                "--config",
                str(config_path),
                "sync",
            ]
        )
    else:
        task_command = subprocess.list2cmdline(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ]
        )
    return [
        "schtasks.exe",
        "/Create",
        "/F",
        "/SC",
        "DAILY",
        "/ST",
        time_text,
        "/TN",
        TASK_NAME,
        "/TR",
        task_command,
    ]


def install(
    config_path: Path,
    *,
    time_text: str,
    python_path: str = sys.executable,
) -> Path:
    parse_time_text(time_text)
    script_path = config_path.parent / "run-sync.ps1"
    log_dir = config_path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        render_powershell(
            python_path=Path(python_path),
            config_path=config_path,
            log_dir=log_dir,
        ),
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        build_create_command(
            python_path=python_path,
            config_path=config_path,
            time_text=time_text,
            script_path=script_path,
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SchedulerError(
            f"Windows 任务计划安装失败：{result.stderr.strip() or result.stdout.strip()}"
        )
    return script_path


def status() -> bool:
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def uninstall() -> bool:
    result = subprocess.run(
        ["schtasks.exe", "/Delete", "/F", "/TN", TASK_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
