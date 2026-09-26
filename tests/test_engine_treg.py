"""Treg page extraction maps routed provider pages into ScrapeResult."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines import get_engine
from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions


async def test_treg_extract_page(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://treg.to/call/treg.web.extract",
        json={
            "output": {
                "pages": [{"url": "https://example.com/final", "markdown": "# Hello\nWorld"}]
            }
        },
        headers={
            "X-Treg-Call-Id": "call-789",
            "X-Treg-Cost-Micro": "3000",
            "X-Treg-Served-By": "tinyfish.web.fetch",
        },
    )
    result = await get_engine("treg")(api_key="test-token").scrape(
        "https://example.com", ScrapeOptions(extra={"treg_max_cost_usd": 0.05})
    )
    request = httpx_mock.get_requests()[0]
    assert request.headers["X-Treg-Token"] == "test-token"
    assert request.headers["X-Treg-Route-Max-Cost"] == "0.05"
    assert json.loads(request.content) == {"url": "https://example.com"}
    assert result.url == "https://example.com/final"
    assert result.markdown == "# Hello\nWorld"
    assert result.text == "Hello\nWorld"
    assert result.cost_usd == 0.003
    assert result.meta["treg_served_by"] == "tinyfish.web.fetch"


@pytest.mark.parametrize(
    "page",
    [
        {"html": "<h1>Hello</h1><p>World</p>"},
        {"raw_content": "# Hello\nWorld"},
        {"markdown_content": "# Hello\nWorld"},
        {"text": "# Hello\nWorld", "format": "markdown"},
        "# Hello\nWorld",
    ],
)
async def test_treg_provider_page_formats(httpx_mock: HTTPXMock, page: object) -> None:
    httpx_mock.add_response(
        json={"output": {"pages": [page]}},
    )
    result = await get_engine("treg")(api_key="test-token").scrape("https://example.com")
    assert "Hello" in result.text and "World" in result.markdown
    assert "#" not in result.text


@pytest.mark.parametrize("status", [401, 402, 503])
async def test_treg_http_error(httpx_mock: HTTPXMock, status: int) -> None:
    httpx_mock.add_response(status_code=status)
    with pytest.raises(EngineError, match=f"HTTP {status}"):
        await get_engine("treg")(api_key="test-token").scrape("https://example.com")


async def test_treg_timeout(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    with pytest.raises(EngineError, match="timed out"):
        await get_engine("treg")(api_key="test-token").scrape(
            "https://example.com", ScrapeOptions(timeout_s=2)
        )
    assert httpx_mock.get_requests()[0].extensions["timeout"]["read"] == 2


def test_treg_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TREG_TOKEN", raising=False)
    assert not get_engine("treg")().is_available()
