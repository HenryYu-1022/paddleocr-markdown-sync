from pathlib import Path

import pytest

from paddleocr_markdown_sync.api import (
    AuthenticationError,
    PaddleOcrClient,
    PaddleOcrError,
)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict | None = None,
        *,
        text: str = "",
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def write_pdf(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.7\n")
    return path


def test_submit_returns_job_id_and_uses_cloud_model(tmp_path):
    session = FakeSession([FakeResponse(200, {"data": {"jobId": "job-1"}})])
    client = PaddleOcrClient("secret", session=session, sleeper=lambda _: None)

    job_id = client.submit(write_pdf(tmp_path / "paper.pdf"))

    assert job_id == "job-1"
    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "bearer secret"
    assert kwargs["data"]["model"] == "PaddleOCR-VL-1.6"


def test_submit_raises_clear_authentication_error_without_token_in_message(tmp_path):
    session = FakeSession([FakeResponse(401, text="bad secret")])
    client = PaddleOcrClient("super-private-token", session=session, sleeper=lambda _: None)

    with pytest.raises(AuthenticationError) as exc_info:
        client.submit(write_pdf(tmp_path / "paper.pdf"))

    assert "Token" in str(exc_info.value)
    assert "super-private-token" not in str(exc_info.value)


def test_server_error_is_retried_with_bounded_backoff(tmp_path):
    delays = []
    session = FakeSession(
        [
            FakeResponse(503, text="temporary"),
            FakeResponse(200, {"data": {"jobId": "job-2"}}),
        ]
    )
    client = PaddleOcrClient(
        "secret",
        session=session,
        max_retries=2,
        sleeper=delays.append,
    )

    assert client.submit(write_pdf(tmp_path / "paper.pdf")) == "job-2"
    assert delays == [1.0]
    assert len(session.calls) == 2


def test_rate_limit_honors_retry_after(tmp_path):
    delays = []
    session = FakeSession(
        [
            FakeResponse(429, headers={"Retry-After": "7"}),
            FakeResponse(200, {"data": {"jobId": "job-3"}}),
        ]
    )
    client = PaddleOcrClient(
        "secret",
        session=session,
        max_retries=2,
        sleeper=delays.append,
    )

    assert client.submit(write_pdf(tmp_path / "paper.pdf")) == "job-3"
    assert delays == [7.0]


def test_get_job_parses_done_result_url():
    session = FakeSession(
        [
            FakeResponse(
                200,
                {
                    "data": {
                        "state": "done",
                        "resultUrl": {"jsonUrl": "https://result.test/file.jsonl"},
                        "extractProgress": {"extractedPages": 8},
                    }
                },
            )
        ]
    )
    client = PaddleOcrClient("secret", session=session, sleeper=lambda _: None)

    result = client.get_job("job-4")

    assert result.state == "done"
    assert result.json_url == "https://result.test/file.jsonl"
    assert result.extracted_pages == 8


def test_download_text_rejects_non_utf8_protocol_failure():
    session = FakeSession([FakeResponse(200, content=b"\xff")])
    client = PaddleOcrClient("secret", session=session, sleeper=lambda _: None)

    with pytest.raises(PaddleOcrError):
        client.download_text("https://result.test/file.jsonl")
