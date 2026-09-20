"""Tests for SerperSearchEngine. All HTTP is mocked; no live Serper calls."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.search.engines.base import SearchEngineError
from scrapefold.search.options import SearchOptions

_ENDPOINT = "https://google.serper.dev/search"
_API_KEY = "serper-test-key"


def _engine(api_key: str = _API_KEY):
    from scrapefold.search.engines.serper_search import SerperSearchEngine

    return SerperSearchEngine(api_key=api_key)


def _json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


def _ok_payload() -> dict:
    return {
        "organic": [
            {"title": "First", "link": "https://one.com", "snippet": "first snippet"},
            {"title": "Second", "link": "https://two.com", "snippet": "second snippet"},
        ]
    }


async def test_success_parses_hits_with_rank_and_engine(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=_ENDPOINT, json=_ok_payload())

    hits = await engine.search("llm inference", SearchOptions(count=5))

    body = _json_body(httpx_mock.get_requests()[0])
    assert httpx_mock.get_requests()[0].headers["X-API-KEY"] == _API_KEY
    assert body["q"] == "llm inference"
    assert body["num"] == 5
    assert "gl" not in body and "hl" not in body
    assert [h.url for h in hits] == ["https://one.com", "https://two.com"]
    assert [h.rank for h in hits] == [0, 1]
    assert all(h.engine == "serper" for h in hits)
    assert hits[0].title == "First"
    assert hits[0].snippet == "first snippet"
    assert hits[0].raw["link"] == "https://one.com"


async def test_country_language_and_extra_forwarded(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=_ENDPOINT, json=_ok_payload())
    opts = SearchOptions(country="us", language="en", extra={"serper_tbs": "qdr:d"})

    await engine.search("q", opts)

    body = _json_body(httpx_mock.get_requests()[0])
    assert body["gl"] == "us"
    assert body["hl"] == "en"
    assert body["tbs"] == "qdr:d"


async def test_http_error_raises_search_engine_error(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="POST", url=_ENDPOINT, status_code=403, json={})

    with pytest.raises(SearchEngineError) as exc_info:
        await engine.search("q")

    assert exc_info.value.engine == "serper"


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    from scrapefold.search.engines.serper_search import SerperSearchEngine

    assert SerperSearchEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "env-serper-key")
    from scrapefold.search.engines.serper_search import SerperSearchEngine

    assert SerperSearchEngine().api_key == "env-serper-key"


def test_engine_registered() -> None:
    from scrapefold.search.engines import get_search_engine, list_search_engine_names

    assert "serper" in list_search_engine_names()
    assert get_search_engine("serper").__name__ == "SerperSearchEngine"
