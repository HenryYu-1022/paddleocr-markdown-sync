from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

import requests

from .api import AuthenticationError, PaddleOcrClient, PaddleOcrError
from .config import (
    ConfigurationError,
    config_path,
    credentials_path,
    load_settings,
    read_token,
    write_initial_config,
)
from .discovery import discover_pdfs
from .models import Settings, SyncSummary
from .scheduler import SchedulerError, scheduler_for_platform
from .sync import sync_library


def check_endpoint(url: str, timeout: int) -> bool:
    try:
        response = requests.head(url, timeout=min(timeout, 10), allow_redirects=True)
    except requests.RequestException:
        return False
    return response.status_code < 500


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paddleocr-md",
        description="使用 PaddleOCR 在线 API 将 PDF 文献库增量转换为 Markdown",
    )
    parser.add_argument("--config", type=Path, help="指定 config.toml")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="创建本地配置和凭据文件")
    init.add_argument("--pdf-root", type=Path, required=True, help="PDF 文献库目录")
    init.add_argument(
        "--markdown-root",
        type=Path,
        required=True,
        help="Markdown 输出目录",
    )
    init.add_argument("--force", action="store_true", help="覆盖已有普通配置")

    convert = commands.add_parser("convert", help="转换一个 PDF")
    convert.add_argument("pdf", type=Path)
    convert.add_argument("--dry-run", action="store_true")
    convert.add_argument("--daily-page-limit", type=int)

    sync = commands.add_parser("sync", help="递归扫描并增量同步 PDF 文献库")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--daily-page-limit", type=int)

    commands.add_parser("status", help="查看 PDF 和转换状态")
    commands.add_parser("doctor", help="检查配置、Token、目录和 API 端点")

    config = commands.add_parser("config", help="查看配置")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("path", help="打印配置与凭据文件位置")

    schedule = commands.add_parser("schedule", help="管理每日自动同步任务")
    schedule_commands = schedule.add_subparsers(
        dest="schedule_command",
        required=True,
    )
    schedule_install = schedule_commands.add_parser("install", help="安装每日任务")
    schedule_install.add_argument(
        "--time",
        default="03:00",
        help="每天运行时间，格式 HH:MM（默认 03:00）",
    )
    schedule_commands.add_parser("status", help="检查每日任务")
    schedule_commands.add_parser("uninstall", help="卸载每日任务")
    return parser


def _print_summary(summary: SyncSummary, *, dry_run: bool) -> None:
    prefix = "计划转换" if dry_run else "同步完成"
    print(
        f"{prefix}：{summary.planned} 个 PDF，"
        f"{summary.planned_pages} 页；"
        f"跳过 {summary.skipped}，延期 {summary.deferred}，"
        f"页数未知 {summary.unknown_pages}。"
    )
    if not dry_run:
        print(
            f"成功 {summary.succeeded}，失败 {summary.failed}，"
            f"恢复任务 {summary.resumed}，新提交 {summary.submitted_pages} 页。"
        )


def _client(settings: Settings) -> PaddleOcrClient:
    token = read_token()
    if not token:
        raise ConfigurationError(
            f"未配置 PADDLE_OCR_TOKEN。请编辑：{credentials_path()}"
        )
    return PaddleOcrClient(
        token,
        timeout=settings.http_timeout,
        max_retries=settings.max_retries,
        job_url=settings.job_url,
        model=settings.model,
    )


def _status(settings: Settings) -> int:
    pdf_count = len(discover_pdfs(settings.pdf_root))
    counts: dict[str, int] = {}
    for metadata_path in settings.markdown_root.rglob("metadata.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            status = str(payload.get("status") or "unknown")
        except (OSError, json.JSONDecodeError):
            status = "invalid"
        counts[status] = counts.get(status, 0) + 1
    print(f"PDF 文件：{pdf_count}")
    print(f"Markdown 文档目录：{sum(counts.values())}")
    for status in sorted(counts):
        print(f"  {status}: {counts[status]}")
    return 0


def _doctor(
    path: Path | None,
    endpoint_checker: Callable[[str, int], bool],
) -> int:
    ok = True
    target = path or config_path()
    print(f"配置文件：{target}")
    try:
        settings = load_settings(target)
        print("  [通过] 配置文件可读取")
    except ConfigurationError as exc:
        print(f"  [失败] {exc}")
        return 2

    if settings.pdf_root.is_dir():
        print(f"  [通过] PDF 目录：{settings.pdf_root}")
    else:
        print(f"  [失败] PDF 目录不存在：{settings.pdf_root}")
        ok = False
    if settings.markdown_root.is_dir():
        print(f"  [通过] Markdown 目录：{settings.markdown_root}")
    else:
        print(f"  [失败] Markdown 目录不存在：{settings.markdown_root}")
        ok = False
    if read_token():
        print("  [通过] 已读取 PADDLE_OCR_TOKEN（内容不会显示）")
    else:
        print(f"  [失败] 未配置 PADDLE_OCR_TOKEN：{credentials_path()}")
        ok = False
    if endpoint_checker(settings.job_url, settings.http_timeout):
        print(f"  [通过] API 端点可达：{settings.job_url}")
    else:
        print(f"  [失败] API 端点不可达：{settings.job_url}")
        ok = False
    return 0 if ok else 2


def main(
    argv: Sequence[str] | None = None,
    *,
    endpoint_checker: Callable[[str, int], bool] = check_endpoint,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "config" and args.config_command == "path":
            print(f"普通配置：{config_path()}")
            print(f"API Token：{credentials_path()}")
            return 0
        if args.command == "init":
            config_file, credentials_file = write_initial_config(
                args.pdf_root,
                args.markdown_root,
                force=args.force,
            )
            print(f"已创建普通配置：{config_file}")
            print(f"API 凭据文件：{credentials_file}")
            print("请打开 credentials.env，把 Token 填到 PADDLE_OCR_TOKEN= 后面。")
            return 0
        if args.command == "doctor":
            return _doctor(args.config, endpoint_checker)
        if args.command == "schedule":
            target_config = args.config or config_path()
            scheduler = scheduler_for_platform()
            if args.schedule_command == "install":
                if not target_config.is_file():
                    raise ConfigurationError(f"配置文件不存在：{target_config}")
                target = scheduler.install(target_config, time_text=args.time)
                print(f"已安装每日同步任务：{target}")
                return 0
            if args.schedule_command == "status":
                installed = scheduler.status()
                print("每日同步任务：已安装" if installed else "每日同步任务：未安装")
                return 0 if installed else 1
            if args.schedule_command == "uninstall":
                removed = scheduler.uninstall()
                print("已卸载每日同步任务" if removed else "每日同步任务原本未安装")
                return 0

        settings = load_settings(args.config or config_path())
        if args.command == "status":
            return _status(settings)
        if args.command in {"sync", "convert"}:
            if args.command == "convert":
                pdf = args.pdf.expanduser()
                if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
                    raise ConfigurationError(f"PDF 文件不存在：{pdf}")
                settings = replace(settings, pdf_root=pdf)
            client = None if args.dry_run else _client(settings)
            summary = sync_library(
                settings,
                client=client,  # type: ignore[arg-type]
                dry_run=args.dry_run,
                daily_page_limit=args.daily_page_limit,
            )
            _print_summary(summary, dry_run=args.dry_run)
            return 1 if summary.failed else 0
    except (
        ConfigurationError,
        AuthenticationError,
        PaddleOcrError,
        SchedulerError,
    ) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断；已提交的任务将在下次同步时恢复。", file=sys.stderr)
        return 130
    return 2
