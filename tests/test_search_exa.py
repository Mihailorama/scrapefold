"""Tests for ExaSearchEngine. All HTTP is mocked; no live Exa calls."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.search.engines.base import SearchEngineError
from scrapefold.search.options import SearchOptions

_ENDPOINT = "https://api.exa.ai/search"
_API_KEY = "exa-test-key"


def _engine(api_key: str = _API_KEY):
    from scrapefold.search.engines.exa_search import ExaSearchEngine

    return ExaSearchEngine(api_key=api_key)


def _json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


def _ok_payload() -> dict:
    return {
        "results": [
            {"title": "First", "url": "https://one.com", "text": "full text one"},
            {"title": "Second", "url": "https://two.com", "highlights": ["hl two"]},
        ]
    }


async def test_success_parses_hits_with_rank_and_engine(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=_ENDPOINT, json=_ok_payload())

    hits = await engine.search("agentic rag", SearchOptions(count=7))

    request = httpx_mock.get_requests()[0]
    body = _json_body(request)
    assert request.headers["x-api-key"] == _API_KEY
    assert body["query"] == "agentic rag"
    assert body["numResults"] == 7
    assert body["type"] == "auto"
    assert [h.url for h in hits] == ["https://one.com", "https://two.com"]
    assert [h.rank for h in hits] == [0, 1]
    assert all(h.engine == "exa" for h in hits)
    # snippet: text preferred, else first highlight
    assert hits[0].snippet == "full text one"
    assert hits[1].snippet == "hl two"


async def test_extra_forwarded_into_body(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=_ENDPOINT, json=_ok_payload())
    opts = SearchOptions(extra={"exa_category": "news"})

    await engine.search("q", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["category"] == "news"


async def test_http_error_raises_search_engine_error(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=_ENDPOINT, status_code=401, json={})

    with pytest.raises(SearchEngineError) as exc_info:
        await engine.search("q")

    assert exc_info.value.engine == "exa"


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from scrapefold.search.engines.exa_search import ExaSearchEngine

    assert ExaSearchEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "env-exa-key")
    from scrapefold.search.engines.exa_search import ExaSearchEngine

    assert ExaSearchEngine().api_key == "env-exa-key"


def test_engine_registered() -> None:
    from scrapefold.search.engines import get_search_engine, list_search_engine_names

    assert "exa" in list_search_engine_names()
    assert get_search_engine("exa").__name__ == "ExaSearchEngine"
