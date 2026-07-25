from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .models import DEFAULT_JOB_URL, DEFAULT_MODEL, JobResult


DEFAULT_OPTIONS = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


class PaddleOcrError(RuntimeError):
    """Base error for PaddleOCR requests and responses."""


class AuthenticationError(PaddleOcrError):
    """The cloud API rejected the configured Token."""


class ProtocolError(PaddleOcrError):
    """The cloud API returned an incomplete or invalid response."""


class PaddleOcrClient:
    def __init__(
        self,
        token: str,
        *,
        session: Any | None = None,
        timeout: int = 120,
        max_retries: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
        job_url: str = DEFAULT_JOB_URL,
        model: str = DEFAULT_MODEL,
    ):
        if not token.strip():
            raise ValueError("PaddleOCR Token 不能为空")
        self._token = token.strip()
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.sleeper = sleeper
        self.job_url = job_url.rstrip("/")
        self.model = model

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self._token}"}

    @staticmethod
    def _rewind_files(kwargs: dict[str, Any]) -> None:
        for value in (kwargs.get("files") or {}).values():
            stream = value[-1] if isinstance(value, tuple) else value
            if hasattr(stream, "seek"):
                stream.seek(0)

    def _request(self, method: str, url: str, **kwargs: Any):
        kwargs.setdefault("timeout", self.timeout)
        last_exception: Exception | None = None

        for attempt in range(self.max_retries + 1):
            if attempt:
                self._rewind_files(kwargs)
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exception = exc
                if attempt >= self.max_retries:
                    raise PaddleOcrError(f"网络请求失败：{type(exc).__name__}") from exc
                self.sleeper(float(2**attempt))
                continue

            status = int(response.status_code)
            if status in (401, 403):
                raise AuthenticationError(
                    "PaddleOCR API 拒绝认证，请检查 Token（PADDLE_OCR_TOKEN）。"
                )
            if status == 429:
                if attempt >= self.max_retries:
                    raise PaddleOcrError("PaddleOCR API 请求过于频繁，重试次数已用完。")
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = float(2**attempt)
                self.sleeper(max(0.0, delay))
                continue
            if 500 <= status < 600:
                if attempt >= self.max_retries:
                    raise PaddleOcrError(f"PaddleOCR API 暂时不可用（HTTP {status}）。")
                self.sleeper(float(2**attempt))
                continue
            if not 200 <= status < 300:
                message = str(getattr(response, "text", ""))[:200]
                raise PaddleOcrError(
                    f"PaddleOCR API 返回 HTTP {status}"
                    + (f"：{message}" if message else "。")
                )
            return response

        raise PaddleOcrError("网络请求失败。") from last_exception

    def submit(self, pdf_path: Path | str) -> str:
        source = Path(pdf_path)
        with source.open("rb") as stream:
            response = self._request(
                "POST",
                self.job_url,
                headers=self._headers,
                data={
                    "model": self.model,
                    "optionalPayload": json.dumps(DEFAULT_OPTIONS),
                },
                files={"file": stream},
            )
        try:
            payload = response.json()
            job_id = payload["data"]["jobId"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("PaddleOCR 提交响应缺少 jobId。") from exc
        if not str(job_id).strip():
            raise ProtocolError("PaddleOCR 提交响应包含空 jobId。")
        return str(job_id)

    def get_job(self, job_id: str) -> JobResult:
        response = self._request(
            "GET",
            f"{self.job_url}/{job_id}",
            headers=self._headers,
        )
        try:
            data = response.json()["data"]
            state = str(data["state"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("PaddleOCR 任务响应缺少 state。") from exc

        progress = data.get("extractProgress") or {}
        extracted_pages = progress.get("extractedPages")
        if extracted_pages is not None:
            try:
                extracted_pages = int(extracted_pages)
            except (TypeError, ValueError):
                extracted_pages = None

        if state == "done":
            json_url = (data.get("resultUrl") or {}).get("jsonUrl")
            if not json_url:
                raise ProtocolError("PaddleOCR 已完成任务缺少 JSONL 下载地址。")
            return JobResult(
                job_id=job_id,
                state=state,
                json_url=str(json_url),
                extracted_pages=extracted_pages,
            )
        if state == "failed":
            return JobResult(
                job_id=job_id,
                state=state,
                error=str(data.get("errorMsg") or "PaddleOCR 任务失败"),
                extracted_pages=extracted_pages,
            )
        if state not in {"pending", "running"}:
            raise ProtocolError(f"PaddleOCR 返回未知任务状态：{state}")
        return JobResult(
            job_id=job_id,
            state=state,
            extracted_pages=extracted_pages,
        )

    def wait(self, job_id: str, *, poll_interval: float = 5.0) -> JobResult:
        while True:
            result = self.get_job(job_id)
            if result.state in {"done", "failed"}:
                return result
            self.sleeper(max(0.0, poll_interval))

    def download_text(self, url: str) -> str:
        response = self._request("GET", url)
        try:
            return bytes(response.content).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("PaddleOCR JSONL 结果不是有效 UTF-8。") from exc

    def download_bytes(self, url: str) -> bytes:
        response = self._request("GET", url)
        return bytes(response.content)
