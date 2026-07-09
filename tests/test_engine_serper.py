"""Tests for SerperEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.serper import SerperEngine, _build_body
from scrapefold.options import ScrapeOptions

_ENDPOINT = "https://scrape.serper.dev"
_API_KEY = "test-api-key-123"

_RESPONSE = {
    "text": "Hello World",
    "markdown": "# Hello\n\nWorld",
    "metadata": {"title": "Serper Test", "og:type": "website"},
    "jsonld": {"@type": "Article", "headline": "Hello"},
    "credits": 2,
}


def _engine(api_key: str = _API_KEY) -> SerperEngine:
    return SerperEngine(api_key=api_key)


# 1. Success: native text/markdown/json populated, cost from credits ------------


async def test_success_populates_slots(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ENDPOINT, json=_RESPONSE)
    result = await _engine().scrape("https://example.com/")

    assert result.text == "Hello World"
    assert result.markdown == "# Hello\n\nWorld"
    assert result.html is None
    assert result.json == {"@type": "Article", "headline": "Hello"}
    assert result.cost_usd == 2 * 0.001
    assert result.engine == "serper"
    assert result.meta["status_code"] == 200
    assert result.meta["title"] == "Serper Test"
    assert result.meta["serper_credits"] == 2


# 2. Auth + body: X-API-KEY header and url in POST body -------------------------


async def test_api_key_header_and_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ENDPOINT, json=_RESPONSE)
    target = "https://target.example.com/page"
    await _engine().scrape(target)

    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    assert request.headers.get("x-api-key") == _API_KEY
    import json as _json

    body = _json.loads(request.content)
    assert body["url"] == target
    assert body["includeMarkdown"] is True


# 3. Adapter: output_format="text" disables includeMarkdown --------------------


def test_build_body_text_format_disables_markdown() -> None:
    body = _build_body("https://x.com", ScrapeOptions(output_format="text"))
    assert body["includeMarkdown"] is False
    # default keeps markdown on
    assert _build_body("https://x.com", ScrapeOptions())["includeMarkdown"] is True


# 4. Adapter: extra["serper_*"] forwarded into the body ------------------------


async def test_extra_serper_prefix_forwarded_to_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ENDPOINT, json=_RESPONSE)
    opts = ScrapeOptions(extra={"serper_country": "us", "unrelated": "x"})
    await _engine().scrape("https://example.com/", opts)

    import json as _json

    body = _json.loads(httpx_mock.get_requests()[0].content)
    assert body["country"] == "us"
    assert "unrelated" not in body


# 5. Vendor error (4xx/5xx) → EngineError --------------------------------------


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ENDPOINT, status_code=403, text="forbidden")
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")
    assert exc_info.value.engine == "serper"


# 6. Network error → EngineError -----------------------------------------------


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")
    assert exc_info.value.engine == "serper"


# 7. Missing markdown → derived from text --------------------------------------


async def test_missing_markdown_derived_from_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ENDPOINT, json={"text": "plain only", "credits": 1})
    result = await _engine().scrape("https://example.com/")
    assert result.text == "plain only"
    assert result.markdown == "plain only"
    assert result.json is None
    assert result.cost_usd == 0.001


# 8. is_available reflects key presence ----------------------------------------


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert SerperEngine(api_key=None).is_available() is False


# 9. Unsupported options silently dropped --------------------------------------


async def test_unsupported_options_do_not_break(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_ENDPOINT, json=_RESPONSE)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(render_js=True))
    assert result.engine == "serper"
