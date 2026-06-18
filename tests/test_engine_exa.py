"""Tests for ExaEngine.

All tests use ``pytest-httpx`` and never call the live Exa API.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

_BASE = "https://api.exa.ai"
_API_KEY = "exa-test-key"


def _engine(api_key: str = _API_KEY):
    from scrapefold.engines.exa import ExaEngine

    return ExaEngine(api_key=api_key)


def _json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


def _ok_payload() -> dict:
    return {
        "requestId": "req_123",
        "results": [
            {
                "title": "Example",
                "url": "https://example.com",
                "text": "Example text",
                "highlights": ["Example highlight"],
            }
        ],
        "costDollars": {"total": 0.007},
    }


async def test_default_contents_request_uses_top_level_text(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/contents", json=_ok_payload())

    result = await engine.scrape("https://example.com/page")

    request = httpx_mock.get_requests()[0]
    body = _json_body(request)
    assert request.headers["x-api-key"] == _API_KEY
    assert body["urls"] == ["https://example.com/page"]
    assert body["text"] is True
    assert "contents" not in body
    assert result.json == _ok_payload()
    assert result.cost_usd == 0.007
    assert result.engine == "exa"
    assert "Example text" in result.text
    assert "Example text" in result.markdown


async def test_contents_all_failed_statuses_raise_engine_error(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/contents",
        json={
            "results": [],
            "statuses": [{"id": "https://bad.example", "status": "error"}],
        },
    )

    with pytest.raises(EngineError) as exc_info:
        await engine.scrape("https://bad.example")

    assert exc_info.value.engine == "exa"
    assert "no successful contents" in str(exc_info.value)


async def test_search_request_nests_contents_options(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())
    opts = ScrapeOptions(
        extra={
            "exa_mode": "search",
            "exa_query": "latest LLM research",
            "exa_contents": {"text": {"maxCharacters": 500}},
            "exa_numResults": 5,
        }
    )

    await engine.scrape("https://ignored.example", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["query"] == "latest LLM research"
    assert body["type"] == "auto"
    assert body["numResults"] == 5
    assert body["contents"] == {"text": {"maxCharacters": 500}}


async def test_linkedin_profile_defaults_to_people_search(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())

    await engine.scrape("https://www.linkedin.com/in/janedoe/")

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "people"
    assert body["contents"] == {"highlights": True}
    assert "linkedin.com/in/janedoe" in body["query"]


async def test_linkedin_company_defaults_to_company_search(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())

    await engine.scrape("https://www.linkedin.com/company/openai/")

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "company"
    assert body["contents"] == {"highlights": True}


async def test_people_category_strips_unsupported_filters(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/search", json=_ok_payload())
    opts = ScrapeOptions(
        extra={
            "exa_mode": "search",
            "exa_query": "VP Engineering at fintech companies",
            "exa_category": "people",
            "exa_includeDomains": ["linkedin.com"],
            "exa_excludeDomains": ["example.com"],
            "exa_startPublishedDate": "2025-01-01",
            "exa_endPublishedDate": "2025-12-31",
        }
    )

    await engine.scrape("https://ignored.example", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "people"
    assert "includeDomains" not in body
    assert "excludeDomains" not in body
    assert "startPublishedDate" not in body
    assert "endPublishedDate" not in body


async def test_find_similar_hits_findsimilar_endpoint(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/findSimilar", json=_ok_payload())
    opts = ScrapeOptions(extra={"exa_mode": "find_similar", "exa_numResults": 3})

    await engine.scrape("https://example.com/source", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["url"] == "https://example.com/source"
    assert body["numResults"] == 3
    assert body["contents"] == {"highlights": True}


async def test_answer_hits_answer_endpoint(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    payload = {"answer": "42", "citations": [], "costDollars": {"total": 0.002}}
    httpx_mock.add_response(method="POST", url=f"{_BASE}/answer", json=payload)
    opts = ScrapeOptions(extra={"exa_mode": "answer", "exa_query": "What is the answer?"})

    result = await engine.scrape("https://ignored.example", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body == {"query": "What is the answer?", "text": True}
    assert result.json == payload
    assert result.cost_usd == 0.002


async def test_answer_streaming_rejected_before_request() -> None:
    opts = ScrapeOptions(extra={"exa_mode": "answer", "exa_stream": True})

    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://ignored.example", opts)

    assert "streaming" in str(exc_info.value)


async def test_agent_creates_run_and_polls_to_completion(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE}/agent/runs",
        json={"id": "agent_run_123", "status": "queued"},
    )
    terminal = {
        "id": "agent_run_123",
        "status": "completed",
        "output": {"text": "done", "structured": {"ok": True}},
        "usage": {"agentComputeUnits": 0.1},
    }
    httpx_mock.add_response(method="GET", url=f"{_BASE}/agent/runs/agent_run_123", json=terminal)
    opts = ScrapeOptions(
        extra={
            "exa_mode": "agent",
            "exa_query": "Find AI infra companies",
            "exa_effort": "minimal",
            "exa_poll_interval_ms": 0,
            "exa_max_poll_ms": 1000,
        }
    )

    result = await engine.scrape("https://ignored.example", opts)

    create_body = _json_body(httpx_mock.get_requests()[0])
    assert create_body["query"] == "Find AI infra companies"
    assert create_body["effort"] == "minimal"
    assert result.json == terminal
    assert "done" in result.text


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=f"{_BASE}/contents", status_code=401, json={})

    with pytest.raises(EngineError) as exc_info:
        await engine.scrape("https://example.com")

    assert exc_info.value.engine == "exa"


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from scrapefold.engines.exa import ExaEngine

    assert ExaEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "env-exa-key")
    from scrapefold.engines.exa import ExaEngine

    assert ExaEngine().api_key == "env-exa-key"


def test_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "exa" in list_engine_names()
    assert get_engine("exa").__name__ == "ExaEngine"
