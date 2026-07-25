from __future__ import annotations

import platform


class SchedulerError(RuntimeError):
    """A platform scheduler command failed."""


class UnsupportedScheduler(SchedulerError):
    """The current platform has no scheduler installer."""


def parse_time_text(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise SchedulerError("时间必须使用 HH:MM 格式，例如 03:15。") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise SchedulerError("定时时间超出有效范围。")
    return hour, minute


def scheduler_for_platform(system: str | None = None):
    name = system or platform.system()
    if name == "Darwin":
        from . import macos

        return macos
    if name == "Windows":
        from . import windows

        return windows
    raise UnsupportedScheduler(f"{name} 暂不支持自动安装定时任务。")
