from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import SchedulerError, parse_time_text


LABEL = "com.henryyu.paddleocr-markdown-sync"


def default_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render_plist(
    *,
    python_path: str,
    config_path: Path,
    hour: int,
    minute: int,
    log_dir: Path,
) -> str:
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            python_path,
            "-m",
            "paddleocr_markdown_sync",
            "--config",
            str(config_path),
            "sync",
        ],
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_dir / "sync.out.log"),
        "StandardErrorPath": str(log_dir / "sync.err.log"),
        "ProcessType": "Background",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False).decode(
        "utf-8"
    )


def install(
    config_path: Path,
    *,
    time_text: str,
    python_path: str = sys.executable,
) -> Path:
    hour, minute = parse_time_text(time_text)
    target = default_plist_path()
    log_dir = config_path.parent / "logs"
    target.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_plist(
            python_path=python_path,
            config_path=config_path,
            hour=hour,
            minute=minute,
            log_dir=log_dir,
        ),
        encoding="utf-8",
    )
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SchedulerError(
            f"launchctl 安装失败：{result.stderr.strip() or result.stdout.strip()}"
        )
    return target


def status() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def uninstall() -> bool:
    target = default_plist_path()
    result = subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    existed = target.exists() or result.returncode == 0
    if target.exists():
        target.unlink()
    return existed
