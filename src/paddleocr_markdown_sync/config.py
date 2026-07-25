from __future__ import annotations

import json
import os
import platform
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from platformdirs import user_config_path

from .models import DEFAULT_JOB_URL, DEFAULT_MODEL, Settings


CONFIG_DIR_ENV = "PADDLEOCR_MD_CONFIG_DIR"
TOKEN_ENV = "PADDLE_OCR_TOKEN"


class ConfigurationError(ValueError):
    """Raised when a configuration file is missing required values."""


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / "paddleocr-markdown-sync"
    return Path(user_config_path("paddleocr-markdown-sync", appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.toml"


def credentials_path() -> Path:
    return config_dir() / "credentials.env"


def logs_dir() -> Path:
    return config_dir() / "logs"


def read_token(path: Path | None = None) -> str:
    environment = os.environ.get(TOKEN_ENV, "").strip()
    if environment:
        return environment

    target = path or credentials_path()
    if not target.is_file():
        return ""
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == TOKEN_ENV:
            return value.strip().strip("\"'")
    return ""


def write_token(token: str, path: Path | None = None) -> Path:
    value = token.strip()
    if not value or "\n" in value or "\r" in value:
        raise ConfigurationError("PaddleOCR Token 为空或包含非法换行。")
    target = path or credentials_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(
            "# PaddleOCR 在线 API Token。本文件不得提交到 Git。\n"
            f"{TOKEN_ENV}={value}\n"
        )
    temporary.replace(target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def load_settings(path: Path | None = None) -> Settings:
    target = path or config_path()
    if not target.is_file():
        raise ConfigurationError(f"配置文件不存在：{target}")
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        library = data["library"]
        pdf_root = Path(library["pdf_root"]).expanduser()
        markdown_root = Path(library["markdown_root"]).expanduser()
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(
            f"配置文件必须包含 [library] 下的 pdf_root 和 markdown_root：{target}"
        ) from exc

    sync = data.get("sync", {})
    api = data.get("api", {})
    return Settings(
        pdf_root=pdf_root,
        markdown_root=markdown_root,
        daily_page_limit=int(sync.get("daily_page_limit", 19000)),
        poll_interval=float(sync.get("poll_interval", 5.0)),
        http_timeout=int(sync.get("http_timeout", 120)),
        max_retries=int(sync.get("max_retries", 3)),
        job_url=str(api.get("job_url", DEFAULT_JOB_URL)),
        model=str(api.get("model", DEFAULT_MODEL)),
    )


def write_initial_config(
    pdf_root: Path | str,
    markdown_root: Path | str,
    *,
    force: bool = False,
) -> tuple[Path, Path]:
    target_config = config_path()
    target_credentials = credentials_path()
    if target_config.exists() and not force:
        raise ConfigurationError(
            f"配置文件已存在：{target_config}。如需覆盖请使用 --force。"
        )

    source = Path(pdf_root).expanduser()
    output = Path(markdown_root).expanduser()
    target_config.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    config_text = (
        "[library]\n"
        f"pdf_root = {json.dumps(str(source), ensure_ascii=False)}\n"
        f"markdown_root = {json.dumps(str(output), ensure_ascii=False)}\n\n"
        "[sync]\n"
        "daily_page_limit = 19000\n"
        "poll_interval = 5.0\n"
        "http_timeout = 120\n"
        "max_retries = 3\n\n"
        "[api]\n"
        f"job_url = {json.dumps(DEFAULT_JOB_URL)}\n"
        f"model = {json.dumps(DEFAULT_MODEL)}\n"
    )
    temporary = target_config.with_name(target_config.name + ".tmp")
    temporary.write_text(config_text, encoding="utf-8")
    temporary.replace(target_config)

    if not target_credentials.exists():
        target_credentials.write_text(
            "# 将 PaddleOCR 在线 API Token 填在等号后面。\n"
            "PADDLE_OCR_TOKEN=\n",
            encoding="utf-8",
        )
    try:
        target_credentials.chmod(0o600)
    except OSError:
        pass
    return target_config, target_credentials
