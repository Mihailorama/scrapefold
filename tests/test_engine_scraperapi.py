"""Tests for ScraperApiEngine. Offline — pytest-httpx, no real network."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.scraperapi import ScraperApiEngine, _adapt
from scrapefold.options import ScrapeOptions

_ENDPOINT = "https://api.scraperapi.com/"
_API_KEY = "test-api-key-123"
_HTML = (
    "<html><head><title>ScraperAPI Test</title></head>"
    "<body><h1>Hello</h1><p>World</p></body></html>"
)


def _engine(api_key: str = _API_KEY) -> ScraperApiEngine:
    return ScraperApiEngine(api_key=api_key)


async def test_html_200_populates_all_slots(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/")

    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.cost_usd == 0.00049
    assert result.engine == "scraperapi"
    assert result.meta["status_code"] == 200
