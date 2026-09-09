"""Offline tests for the Keenable REST engine."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.keenable import KeenableEngine, _build_request
from scrapefold.options import ScrapeOptions

_BASE = "https://api.keenable.ai"
_TARGET = "https://example.com/article"
_FETCH = {
    "url": _TARGET,
    "title": "Example",
    "description": "An example page",
    "content": "# Example\n\nClean **markdown**.",
}


async def test_keyed_fetch_returns_native_markdown(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/v1/fetch?url={_TARGET}", json=_FETCH)

    result = await KeenableEngine(api_key="keen_test").scrape(_TARGET)

    request = httpx_mock.get_requests()[0]
    assert request.headers["x-api-key"] == "keen_test"
    assert result.markdown == _FETCH["content"]
    assert "Clean" in result.text
    assert result.json == _FETCH
    assert result.meta["title"] == "Example"
    assert result.cost_usd == 0.004


async def test_keyless_fetch_uses_public_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{_BASE}/v1/fetch/public?url={_TARGET}", json=_FETCH)

    result = await KeenableEngine(api_key=None).scrape(_TARGET)

    request = httpx_mock.get_requests()[0]
    assert request.headers["x-keenable-title"] == "scrapefold"
    assert "x-api-key" not in request.headers
    assert result.cost_usd == 0.0


def test_fetch_adapter_maps_supported_extras() -> None:
    mode, params = _build_request(
        _TARGET,
        ScrapeOptions(
            extra={
                "keenable_live": True,
                "keenable_max_chars": 1234,
                "keenable_prompt": "Extract prices",
                "unrelated": "drop",
            }
        ),
    )

    assert mode == "fetch"
    assert params == {
        "url": _TARGET,
        "live": True,
        "max_chars": 1234,
        "prompt": "Extract prices",
    }


async def test_search_mode_returns_structured_results(httpx_mock: HTTPXMock) -> None:
    payload = {
        "query": "async Python",
        "results": [{"title": "Result", "url": _TARGET, "description": "Docs"}],
    }
    httpx_mock.add_response(method="POST", url=f"{_BASE}/v1/search", json=payload)
    opts = ScrapeOptions(
        extra={
            "keenable_mode": "search",
            "keenable_query": "async Python",
            "keenable_site": "python.org",
            "keenable_max_results": 3,
        }
    )

    result = await KeenableEngine(api_key="keen_test").scrape("ignored", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {"query": "async Python", "site": "python.org", "max_results": 3}
    assert result.json == payload
    assert "Result" in result.markdown
    assert result.meta["keenable_mode"] == "search"


async def test_vendor_error_is_wrapped(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_BASE}/v1/fetch?url={_TARGET}",
        status_code=401,
        json={"error": "Authentication failed", "message": "Invalid API key"},
    )

    with pytest.raises(EngineError, match="401: Invalid API key"):
        await KeenableEngine(api_key="bad").scrape(_TARGET)


async def test_timeout_uses_unified_option(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("slow"))

    with pytest.raises(EngineError, match="slow"):
        await KeenableEngine(api_key="keen_test").scrape(_TARGET, ScrapeOptions(timeout_s=7))

    assert httpx_mock.get_requests()[0].extensions["timeout"]["read"] == 7.0


def test_env_key_and_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEENABLE_API_KEY", "keen_env")
    from scrapefold.engines import get_engine, list_engine_names

    assert KeenableEngine().api_key == "keen_env"
    assert KeenableEngine(api_key=None).is_available() is True
    assert "keenable" in list_engine_names()
    assert get_engine("keenable") is KeenableEngine
