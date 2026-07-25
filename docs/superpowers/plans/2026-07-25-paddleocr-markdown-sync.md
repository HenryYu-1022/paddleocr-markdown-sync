# PaddleOCR Markdown Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and publicly release a cross-platform Python CLI that recursively converts every PDF in a local library through the PaddleOCR online API into independent Markdown document directories.

**Architecture:** A small `src`-layout package separates configuration, PDF discovery and quota planning, PaddleOCR HTTP calls, result export, synchronization, and platform schedulers. Each PDF is an independent document keyed by its relative source path; completed unchanged paths are skipped, while different paths are never content-deduplicated. Tests use fake HTTP sessions and temporary files so CI never consumes the real API quota.

**Tech Stack:** Python 3.10+, argparse, requests, pypdf, platformdirs, pytest, hatchling, GitHub Actions, launchd, Windows Task Scheduler.

---

## File Map

```text
pyproject.toml                                      # package metadata, dependencies, CLI entry
README.md                                           # Chinese installation/configuration/workflow guide
.env.example                                       # safe token placeholder
.gitignore                                         # secrets, caches, builds, local outputs
LICENSE                                            # MIT license
src/paddleocr_markdown_sync/__init__.py             # package version
src/paddleocr_markdown_sync/__main__.py             # python -m entry
src/paddleocr_markdown_sync/models.py               # immutable configuration and task models
src/paddleocr_markdown_sync/config.py               # platform paths, TOML and credentials
src/paddleocr_markdown_sync/discovery.py            # recursive scan, fingerprints, page budget
src/paddleocr_markdown_sync/api.py                  # PaddleOCR submission, polling and downloads
src/paddleocr_markdown_sync/exporter.py             # safe JSONL materialization
src/paddleocr_markdown_sync/sync.py                 # incremental/resumable orchestration
src/paddleocr_markdown_sync/cli.py                  # user-facing commands and exit codes
src/paddleocr_markdown_sync/scheduler/__init__.py   # scheduler dispatch
src/paddleocr_markdown_sync/scheduler/macos.py      # launchd rendering/install/status/uninstall
src/paddleocr_markdown_sync/scheduler/windows.py    # Task Scheduler command rendering
tests/test_config.py
tests/test_discovery.py
tests/test_api.py
tests/test_exporter.py
tests/test_sync.py
tests/test_cli.py
tests/test_scheduler.py
.github/workflows/tests.yml                         # three-OS tests and build
```

### Task 1: Package Skeleton, Models, and Configuration

**Files:**
- Create: `pyproject.toml`
- Create: `src/paddleocr_markdown_sync/__init__.py`
- Create: `src/paddleocr_markdown_sync/models.py`
- Create: `src/paddleocr_markdown_sync/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
from pathlib import Path

from paddleocr_markdown_sync.config import (
    credentials_path,
    load_settings,
    read_token,
)


def test_environment_token_overrides_credentials_file(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials.env"
    credentials.write_text("PADDLE_OCR_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.setenv("PADDLE_OCR_TOKEN", "environment-token")
    assert read_token(credentials) == "environment-token"


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


def test_credentials_path_uses_platform_config_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path))
    assert credentials_path() == tmp_path / "credentials.env"
```

- [ ] **Step 2: Run tests and verify the expected import failure**

Run: `python3 -m pytest tests/test_config.py -q`

Expected: FAIL because `paddleocr_markdown_sync.config` does not exist.

- [ ] **Step 3: Add package metadata and minimal configuration implementation**

```python
# models.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    pdf_root: Path
    markdown_root: Path
    daily_page_limit: int = 19000
    poll_interval: float = 5.0
    http_timeout: int = 120
    max_retries: int = 3
```

```python
# config.py
import os
import tomllib
from pathlib import Path

from platformdirs import user_config_path

from .models import Settings


def config_dir() -> Path:
    override = os.environ.get("PADDLEOCR_MD_CONFIG_DIR")
    return Path(override) if override else user_config_path("paddleocr-markdown-sync")


def credentials_path() -> Path:
    return config_dir() / "credentials.env"


def read_token(path: Path | None = None) -> str:
    environment = os.environ.get("PADDLE_OCR_TOKEN", "").strip()
    if environment:
        return environment
    target = path or credentials_path()
    if not target.is_file():
        return ""
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("PADDLE_OCR_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def load_settings(path: Path) -> Settings:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    library = data["library"]
    sync = data.get("sync", {})
    return Settings(
        pdf_root=Path(library["pdf_root"]).expanduser(),
        markdown_root=Path(library["markdown_root"]).expanduser(),
        daily_page_limit=int(sync.get("daily_page_limit", 19000)),
        poll_interval=float(sync.get("poll_interval", 5.0)),
        http_timeout=int(sync.get("http_timeout", 120)),
        max_retries=int(sync.get("max_retries", 3)),
    )
```

- [ ] **Step 4: Run configuration tests**

Run: `python3 -m pytest tests/test_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src tests/test_config.py
git commit -m "feat: add package configuration"
```

### Task 2: Recursive PDF Discovery and Daily Page Budget

**Files:**
- Create: `src/paddleocr_markdown_sync/discovery.py`
- Modify: `src/paddleocr_markdown_sync/models.py`
- Test: `tests/test_discovery.py`

- [ ] **Step 1: Write failing recursive discovery tests**

```python
from pathlib import Path

from pypdf import PdfWriter

from paddleocr_markdown_sync.discovery import discover_pdfs, plan_daily_work


def write_pdf(path: Path, pages: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    with path.open("wb") as stream:
        writer.write(stream)


def test_discovery_recurses_all_levels_and_accepts_uppercase(tmp_path):
    write_pdf(tmp_path / "root.pdf")
    write_pdf(tmp_path / "a" / "b" / "deep.PDF")
    assert [p.relative_to(tmp_path).as_posix() for p in discover_pdfs(tmp_path)] == [
        "a/b/deep.PDF",
        "root.pdf",
    ]


def test_identical_bytes_at_different_paths_are_not_deduplicated(tmp_path):
    write_pdf(tmp_path / "first.pdf")
    (tmp_path / "copy.pdf").write_bytes((tmp_path / "first.pdf").read_bytes())
    assert len(discover_pdfs(tmp_path)) == 2


def test_daily_plan_stops_before_exceeding_page_limit(tmp_path):
    write_pdf(tmp_path / "a.pdf", 2)
    write_pdf(tmp_path / "b.pdf", 3)
    plan = plan_daily_work(tmp_path, tmp_path / "out", daily_page_limit=4)
    assert [item.source_path.name for item in plan] == ["a.pdf"]
```

- [ ] **Step 2: Verify tests fail because discovery is missing**

Run: `python3 -m pytest tests/test_discovery.py -q`

Expected: FAIL with missing module or functions.

- [ ] **Step 3: Implement recursive discovery, document IDs, fingerprints, and budget**

```python
def discover_pdfs(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )


def document_id(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
    stem = safe_stem(path.stem)
    return f"{stem}-{digest}"


def fingerprint(path: Path) -> FileFingerprint:
    stat = path.stat()
    return FileFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
```

`plan_daily_work` must estimate pages with `PdfReader`, include each path independently, skip only a completed matching fingerprint, and stop before the next known page count would exceed the configured limit.

- [ ] **Step 4: Run discovery tests**

Run: `python3 -m pytest tests/test_discovery.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paddleocr_markdown_sync/discovery.py src/paddleocr_markdown_sync/models.py tests/test_discovery.py
git commit -m "feat: add recursive PDF discovery"
```

### Task 3: PaddleOCR Online API Client

**Files:**
- Create: `src/paddleocr_markdown_sync/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API behavior tests using a fake session**

```python
import pytest

from paddleocr_markdown_sync.api import AuthenticationError, PaddleOcrClient


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_submit_returns_job_id(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    session = type("Session", (), {
        "post": lambda self, *a, **k: FakeResponse(200, {"data": {"jobId": "job-1"}})
    })()
    client = PaddleOcrClient("secret", session=session, sleeper=lambda _: None)
    assert client.submit(pdf) == "job-1"


def test_submit_raises_clear_authentication_error(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    session = type("Session", (), {
        "post": lambda self, *a, **k: FakeResponse(401, text="unauthorized")
    })()
    client = PaddleOcrClient("bad", session=session, sleeper=lambda _: None)
    with pytest.raises(AuthenticationError):
        client.submit(pdf)
```

- [ ] **Step 2: Run tests and verify missing client failures**

Run: `python3 -m pytest tests/test_api.py -q`

Expected: FAIL because `api.py` does not exist.

- [ ] **Step 3: Implement bounded retries, submission, polling, and result download**

```python
class PaddleOcrClient:
    def __init__(self, token, *, session=None, timeout=120, max_retries=3, sleeper=time.sleep):
        if not token.strip():
            raise ValueError("PaddleOCR Token 不能为空")
        self.token = token.strip()
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleeper = sleeper

    @property
    def headers(self):
        return {"Authorization": f"bearer {self.token}"}

    def submit(self, pdf_path: Path) -> str:
        with pdf_path.open("rb") as stream:
            response = self._request(
                "post",
                JOB_URL,
                headers=self.headers,
                data={"model": MODEL, "optionalPayload": json.dumps(DEFAULT_OPTIONS)},
                files={"file": stream},
            )
        payload = response.json()
        try:
            return str(payload["data"]["jobId"])
        except (KeyError, TypeError) as exc:
            raise ProtocolError("PaddleOCR 响应缺少 jobId") from exc
```

`_request` must special-case 401/403, honor 429 `Retry-After`, retry timeouts and 5xx with bounded exponential delays, and never include headers in exceptions. `poll` returns a typed result for pending/running/done/failed. `download_text` retrieves the JSONL result URL.

- [ ] **Step 4: Run API tests**

Run: `python3 -m pytest tests/test_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paddleocr_markdown_sync/api.py tests/test_api.py
git commit -m "feat: add PaddleOCR API client"
```

### Task 4: Safe JSONL Exporter

**Files:**
- Create: `src/paddleocr_markdown_sync/exporter.py`
- Test: `tests/test_exporter.py`

- [ ] **Step 1: Write failing materialization and traversal tests**

```python
import json

import pytest

from paddleocr_markdown_sync.exporter import UnsafeOutputPath, materialize_jsonl


def record(markdown_text, images=None):
    return json.dumps({
        "result": {
            "layoutParsingResults": [{
                "markdown": {"text": markdown_text, "images": images or {}},
                "outputImages": {},
            }]
        }
    })


def test_materialize_writes_numbered_pages(tmp_path):
    materialize_jsonl("\n".join([record("# One"), record("# Two")]), tmp_path)
    assert (tmp_path / "page_0001.md").read_text(encoding="utf-8") == "# One"
    assert (tmp_path / "page_0002.md").read_text(encoding="utf-8") == "# Two"


def test_materialize_rejects_parent_traversal(tmp_path):
    with pytest.raises(UnsafeOutputPath):
        materialize_jsonl(record("# One", {"../secret.png": "https://example.test/x"}), tmp_path)
```

- [ ] **Step 2: Verify exporter tests fail**

Run: `python3 -m pytest tests/test_exporter.py -q`

Expected: FAIL because the exporter is missing.

- [ ] **Step 3: Implement safe paths and transactional export**

```python
def safe_relative_path(raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise UnsafeOutputPath(raw)
    return candidate


def materialize_jsonl(text: str, target: Path, *, downloader=download_bytes) -> int:
    target.mkdir(parents=True, exist_ok=True)
    page_number = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        for result in payload["result"]["layoutParsingResults"]:
            page_number += 1
            (target / f"page_{page_number:04d}.md").write_text(
                result["markdown"]["text"],
                encoding="utf-8",
            )
            for raw_path, url in result["markdown"].get("images", {}).items():
                relative = safe_relative_path(raw_path)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(downloader(url))
    (target / "result.jsonl").write_text(text, encoding="utf-8")
    return page_number
```

The sync layer will call this inside a sibling temporary directory and atomically replace the completed output.

- [ ] **Step 4: Run exporter tests**

Run: `python3 -m pytest tests/test_exporter.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paddleocr_markdown_sync/exporter.py tests/test_exporter.py
git commit -m "feat: export PaddleOCR Markdown safely"
```

### Task 5: Incremental and Resumable Synchronization

**Files:**
- Create: `src/paddleocr_markdown_sync/sync.py`
- Modify: `src/paddleocr_markdown_sync/models.py`
- Test: `tests/test_sync.py`

- [ ] **Step 1: Write failing skip and resume tests**

```python
def test_done_matching_fingerprint_is_skipped(tmp_path, configured_settings, fake_client):
    pdf = write_pdf(configured_settings.pdf_root / "paper.pdf")
    output = output_for(pdf, configured_settings)
    write_metadata(output, pdf, status="done")
    result = sync_library(configured_settings, fake_client)
    assert result.skipped == 1
    assert fake_client.submitted == []


def test_submitted_job_is_resumed_without_resubmitting(tmp_path, configured_settings, fake_client):
    pdf = write_pdf(configured_settings.pdf_root / "paper.pdf")
    output = output_for(pdf, configured_settings)
    write_metadata(output, pdf, status="submitted", job_id="existing-job")
    fake_client.done_jobs["existing-job"] = SAMPLE_JSONL
    result = sync_library(configured_settings, fake_client)
    assert result.succeeded == 1
    assert fake_client.submitted == []
    assert (output / "page_0001.md").is_file()
```

- [ ] **Step 2: Run tests and verify synchronization is missing**

Run: `python3 -m pytest tests/test_sync.py -q`

Expected: FAIL because `sync.py` does not exist.

- [ ] **Step 3: Implement metadata state transitions and orchestration**

```python
def process_item(item: WorkItem, client: PaddleOcrClient, settings: Settings) -> bool:
    metadata = read_or_create_metadata(item)
    job_id = metadata.job_id
    if not job_id:
        job_id = client.submit(item.source_path)
        write_metadata(item.output_dir, metadata.submitted(job_id))
    result = client.wait(job_id, poll_interval=settings.poll_interval)
    if result.state == "failed":
        write_metadata(item.output_dir, metadata.failed(result.error))
        return False
    jsonl_text = client.download_text(result.json_url)
    staging = item.output_dir.with_name(item.output_dir.name + ".staging")
    materialize_jsonl(jsonl_text, staging, downloader=client.download_bytes)
    finalize_output(staging, item.output_dir, metadata.done(result))
    return True
```

`sync_library` plans work, processes items sequentially, aborts new submissions on authentication errors, preserves per-document failures, and returns counts for planned, skipped, succeeded, failed, and deferred pages.

- [ ] **Step 4: Run sync tests**

Run: `python3 -m pytest tests/test_sync.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paddleocr_markdown_sync/sync.py src/paddleocr_markdown_sync/models.py tests/test_sync.py
git commit -m "feat: add resumable library sync"
```

### Task 6: Cross-Platform CLI and Doctor

**Files:**
- Create: `src/paddleocr_markdown_sync/cli.py`
- Create: `src/paddleocr_markdown_sync/__main__.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
from paddleocr_markdown_sync.cli import main


def test_config_path_prints_credentials_location(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path))
    assert main(["config", "path"]) == 0
    output = capsys.readouterr().out
    assert str(tmp_path / "credentials.env") in output


def test_doctor_returns_two_when_token_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("PADDLE_OCR_TOKEN", raising=False)
    monkeypatch.setenv("PADDLEOCR_MD_CONFIG_DIR", str(tmp_path))
    assert main(["doctor"]) == 2
    assert "PADDLE_OCR_TOKEN" in capsys.readouterr().out
```

- [ ] **Step 2: Verify CLI tests fail**

Run: `python3 -m pytest tests/test_cli.py -q`

Expected: FAIL because CLI is missing.

- [ ] **Step 3: Implement argparse commands and stable exit codes**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paddleocr-md",
        description="使用 PaddleOCR 在线 API 将 PDF 文献库增量转换为 Markdown",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    convert = commands.add_parser("convert")
    convert.add_argument("pdf", type=Path)
    commands.add_parser("sync")
    commands.add_parser("status")
    commands.add_parser("doctor")
    config = commands.add_parser("config")
    config.add_subparsers(dest="config_command", required=True).add_parser("path")
    return parser
```

`init` writes safe templates only when absent and sets restrictive credential permissions where supported. `doctor` checks config readability, source/output directories, token presence, and endpoint reachability without submitting a PDF. `convert`, `sync`, and `status` use the same settings and metadata code.

- [ ] **Step 4: Run CLI tests and installed help**

Run: `python3 -m pytest tests/test_cli.py -q && python3 -m paddleocr_markdown_sync --help`

Expected: tests PASS and Chinese help lists all commands.

- [ ] **Step 5: Commit**

```bash
git add src/paddleocr_markdown_sync/cli.py src/paddleocr_markdown_sync/__main__.py tests/test_cli.py
git commit -m "feat: add cross-platform CLI"
```

### Task 7: macOS and Windows Daily Schedulers

**Files:**
- Create: `src/paddleocr_markdown_sync/scheduler/__init__.py`
- Create: `src/paddleocr_markdown_sync/scheduler/macos.py`
- Create: `src/paddleocr_markdown_sync/scheduler/windows.py`
- Modify: `src/paddleocr_markdown_sync/cli.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing rendering tests**

```python
from paddleocr_markdown_sync.scheduler.macos import render_plist
from paddleocr_markdown_sync.scheduler.windows import build_create_command


def test_macos_plist_runs_daily_sync_at_requested_time(tmp_path):
    plist = render_plist(
        python_path="/usr/bin/python3",
        config_path=tmp_path / "config.toml",
        hour=3,
        minute=15,
        log_dir=tmp_path / "logs",
    )
    assert "<integer>3</integer>" in plist
    assert "<integer>15</integer>" in plist
    assert "<string>sync</string>" in plist


def test_windows_task_uses_current_user_daily_schedule(tmp_path):
    command = build_create_command(
        python_path=r"C:\Python\python.exe",
        config_path=tmp_path / "config.toml",
        time_text="03:15",
    )
    assert command[:3] == ["schtasks.exe", "/Create", "/F"]
    assert "/SC" in command and "DAILY" in command
    assert "03:15" in command
```

- [ ] **Step 2: Verify scheduler tests fail**

Run: `python3 -m pytest tests/test_scheduler.py -q`

Expected: FAIL because scheduler modules do not exist.

- [ ] **Step 3: Implement platform dispatch and exact install/status/uninstall targets**

macOS uses the label `com.henryyu.paddleocr-markdown-sync` and a calendar interval. Windows uses the task name `PaddleOCR Markdown Sync`. Both invoke `python -m paddleocr_markdown_sync sync --config <absolute-path>` and write logs under the user configuration directory.

```python
def scheduler_for_platform(system: str | None = None):
    name = system or platform.system()
    if name == "Darwin":
        return macos
    if name == "Windows":
        return windows
    raise UnsupportedScheduler("当前平台不支持自动安装定时任务")
```

- [ ] **Step 4: Run scheduler and full unit tests**

Run: `python3 -m pytest tests/test_scheduler.py -q && python3 -m pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paddleocr_markdown_sync/scheduler src/paddleocr_markdown_sync/cli.py tests/test_scheduler.py
git commit -m "feat: add macOS and Windows schedulers"
```

### Task 8: Chinese README, Examples, License, and CI

**Files:**
- Create: `README.md`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `.github/workflows/tests.yml`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add a documentation contract test**

```python
def test_readme_documents_exact_token_destination():
    text = Path("README.md").read_text(encoding="utf-8")
    assert "PADDLE_OCR_TOKEN=" in text
    assert "credentials.env" in text
    assert "paddleocr-md config path" in text
    assert "%APPDATA%" in text
    assert "Application Support" in text
    assert "所有子文件夹" in text
    assert "不进行 PDF 内容去重" in text
    assert "正文和 SI" in text
```

- [ ] **Step 2: Run the contract test and verify README is missing**

Run: `python3 -m pytest tests/test_readme.py -q`

Expected: FAIL because `README.md` does not exist.

- [ ] **Step 3: Write the complete Chinese README and public repository files**

README sections must include:

1. What the tool does and that OCR runs in the PaddleOCR cloud quota.
2. Python installation on macOS and Windows.
3. `paddleocr-md init`.
4. Exact `credentials.env` paths and exact `PADDLE_OCR_TOKEN=...` line.
5. Temporary environment variable examples for zsh and PowerShell.
6. PDF and Markdown root configuration.
7. Recursive scanning of every child directory.
8. Explicit statement that identical content at different paths is converted independently.
9. Explicit statement that main papers, SI, ESI, and attachments stay independent.
10. Manual `convert`, `sync`, `status`, and `doctor` commands.
11. macOS and Windows scheduled task commands.
12. Output directory format.
13. Agent direct-reading prompt example.
14. RAG guidance that grouping main/SI belongs to the indexing layer.
15. Troubleshooting for missing token, 401/403, 429, network errors, and interrupted jobs.

CI matrix:

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with:
      python-version: "3.12"
  - run: python -m pip install -e ".[test]"
  - run: python -m pytest -q
  - run: python -m build
```

- [ ] **Step 4: Run README contract, full tests, and package build**

Run: `python3 -m pytest -q && python3 -m build`

Expected: all tests PASS and both wheel and sdist appear under `dist/`.

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example .gitignore LICENSE .github pyproject.toml tests/test_readme.py
git commit -m "docs: add Chinese workflow guide"
```

### Task 9: Local Configuration Migration and End-to-End Verification

**Files:**
- Create locally, never commit: platform `credentials.env`
- Create locally, never commit: platform `config.toml`

- [ ] **Step 1: Install the package in an isolated virtual environment**

Run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

Expected: installation succeeds and `.venv/bin/paddleocr-md --help` exits 0.

- [ ] **Step 2: Initialize the user configuration**

Run: `.venv/bin/paddleocr-md init`

Expected: command prints the exact `config.toml` and `credentials.env` paths without printing a Token.

- [ ] **Step 3: Securely migrate the existing Token**

Read `PADDLE_OCR_TOKEN` from an existing private `.env` in-process, write only the value to the new user-level `credentials.env`, and set file mode `0600` on macOS. Do not echo either file’s contents.

Expected checks:

```text
source_token=present
destination_token=present
destination_mode=600
```

- [ ] **Step 4: Configure existing local library paths**

Write the local, uncommitted `config.toml` with:

```toml
[library]
pdf_root = "/path/to/private/PDF-library"
markdown_root = "/path/to/private/markdown-output"

[sync]
daily_page_limit = 19000
poll_interval = 5.0
http_timeout = 120
max_retries = 3
```

- [ ] **Step 5: Run non-consuming local checks**

Run:

```bash
.venv/bin/paddleocr-md doctor
.venv/bin/paddleocr-md status
.venv/bin/paddleocr-md sync --dry-run
```

Expected: Token and directories are detected, existing output metadata is readable, and dry-run recursively plans without submitting jobs.

- [ ] **Step 6: Run final verification**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m build
git diff --check
git status --short
```

Expected: tests and build succeed, diff check is clean, and only intentional files are tracked.

### Task 10: Public GitHub Release

**Files:**
- No new source files unless final verification requires corrections.

- [ ] **Step 1: Scan tracked content for secrets and local private paths**

Run:

```bash
git grep -nE 'PADDLE_OCR_TOKEN=[^把<]|Authorization: bearer|aafb[0-9a-f]+'
git grep -nE '(/Users/[^/]+/Library/CloudStorage|[A-Za-z]:\\\\Users\\\\[^\\\\]+)'
```

Expected: no real Token, Authorization value, or private local PDF path in publishable README/source/config examples. The design document may name migration source paths; if the repository is public, replace user-specific design paths with documented placeholders before publication.

- [ ] **Step 2: Confirm GitHub authentication and repository availability**

Run:

```bash
gh auth status
gh repo view HenryYu-1022/paddleocr-markdown-sync
```

Expected: authenticated as `HenryYu-1022`; repository lookup returns not found before creation.

- [ ] **Step 3: Create and push the public repository**

Run:

```bash
gh repo create HenryYu-1022/paddleocr-markdown-sync \
  --public \
  --source=. \
  --remote=origin \
  --push \
  --description "通过 PaddleOCR 在线 API 将 PDF 文献库增量转换为 Markdown"
```

Expected: public repository is created, `origin` is configured, and `main` is pushed.

- [ ] **Step 4: Verify remote state and GitHub Actions**

Run:

```bash
gh repo view HenryYu-1022/paddleocr-markdown-sync --json nameWithOwner,visibility,url,defaultBranchRef
gh run list --repo HenryYu-1022/paddleocr-markdown-sync --limit 3
```

Expected: visibility is `PUBLIC`, default branch is `main`, and the test workflow is queued or running.

- [ ] **Step 5: Record final release evidence**

Report:

- local project path
- public repository URL
- final commit
- test count and result
- package build result
- local Token migration status without revealing the Token
- schedule installation status
- any live API conversion intentionally not run to preserve quota

## Plan Self-Review

- Spec coverage: recursive scanning, no duplicate detection, independent SI/main outputs, cloud-only OCR, daily quota, resume behavior, macOS/Windows scheduling, Chinese README, safe Token setup, Agent workflow, CI, local migration, and public release are each assigned to a task.
- Placeholder scan: implementation steps contain concrete functions, commands, expected outcomes, file paths, and README requirements; no feature is deferred with an unspecified TODO.
- Type consistency: `Settings`, `WorkItem`, `FileFingerprint`, API job results, metadata state, and sync result are introduced before consumers; command names match the approved design.
