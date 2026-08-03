import asyncio

import httpx
import pytest

from app import model_client
from app.model_client import ModelHTTPError, parse_model_json


def test_parse_model_json_returns_structured_payload():
    result = parse_model_json('{"facts":[{"content":"有证据"}]}', "chapter_summary")

    assert result["status"] == "ok"
    assert result["task_type"] == "chapter_summary"
    assert result["parsed_json"] == {"facts": [{"content": "有证据"}]}
    assert result["raw_json"] == '{"facts":[{"content":"有证据"}]}'
    assert result["evidence_required"] is True


def test_parse_model_json_preserves_invalid_output():
    result = parse_model_json("not json", "evidence_qa")

    assert result["status"] == "invalid_model_json"
    assert result["task_type"] == "evidence_qa"
    assert result["raw_json"] == "not json"
    assert "parse_error" in result
    assert result["evidence_required"] is True


class _FakeResponse:
    def __init__(self, status_code: int = 200, content: str = '{"status":"ok","task_type":"chapter_summary"}'):
        self.status_code = status_code
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.post_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        self.post_count += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _install_fake_client(monkeypatch, responses):
    fake = _FakeAsyncClient(responses)
    monkeypatch.setattr(model_client.httpx, "AsyncClient", lambda **kwargs: fake)
    return fake


async def _no_sleep(*args, **kwargs):
    return None


def _run(coro):
    return asyncio.run(coro)


def test_call_openai_compatible_retries_read_timeout_then_succeeds(monkeypatch):
    fake = _install_fake_client(monkeypatch, [httpx.ReadTimeout("timed out"), _FakeResponse()])
    monkeypatch.setattr(model_client.asyncio, "sleep", _no_sleep)

    result = _run(
        model_client.call_openai_compatible(
            task_type="chapter_summary", user_payload="payload", model="gpt-test", api_key="sk-test"
        )
    )

    assert result["status"] == "ok"
    assert fake.post_count == 2


def test_call_openai_compatible_retries_429_then_succeeds(monkeypatch):
    fake = _install_fake_client(monkeypatch, [_FakeResponse(429), _FakeResponse()])
    monkeypatch.setattr(model_client.asyncio, "sleep", _no_sleep)

    result = _run(
        model_client.call_openai_compatible(
            task_type="chapter_summary", user_payload="payload", model="gpt-test", api_key="sk-test"
        )
    )

    assert result["status"] == "ok"
    assert fake.post_count == 2


def test_call_openai_compatible_retries_5xx_then_succeeds(monkeypatch):
    fake = _install_fake_client(monkeypatch, [_FakeResponse(503), _FakeResponse()])
    monkeypatch.setattr(model_client.asyncio, "sleep", _no_sleep)

    result = _run(
        model_client.call_openai_compatible(
            task_type="chapter_summary", user_payload="payload", model="gpt-test", api_key="sk-test"
        )
    )

    assert result["status"] == "ok"
    assert fake.post_count == 2


def test_call_openai_compatible_does_not_retry_4xx(monkeypatch):
    fake = _install_fake_client(monkeypatch, [_FakeResponse(400), _FakeResponse()])
    monkeypatch.setattr(model_client.asyncio, "sleep", _no_sleep)

    with pytest.raises(ModelHTTPError) as exc_info:
        _run(
            model_client.call_openai_compatible(
                task_type="chapter_summary", user_payload="payload", model="gpt-test", api_key="sk-test"
            )
        )

    assert exc_info.value.status_code == 400
    assert fake.post_count == 1


def test_call_openai_compatible_gives_up_after_retries_exhausted(monkeypatch):
    fake = _install_fake_client(
        monkeypatch, [httpx.ReadTimeout("timed out"), httpx.ReadTimeout("timed out")]
    )
    monkeypatch.setattr(model_client.asyncio, "sleep", _no_sleep)

    with pytest.raises(httpx.ReadTimeout):
        _run(
            model_client.call_openai_compatible(
                task_type="chapter_summary", user_payload="payload", model="gpt-test", api_key="sk-test"
            )
        )

    assert fake.post_count == 2
