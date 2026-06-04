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


async def test_render_js_true_sends_render_true_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(render_js=True))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("render") == "true"


async def test_render_js_false_sends_render_false_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(render_js=False))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("render") == "false"


async def test_country_maps_to_country_code(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(country="ru"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("country_code") == "ru"


async def test_premium_proxy_sends_premium_true(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(premium_proxy=True))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("premium") == "true"


async def test_wait_for_selector_forwarded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(wait_for_selector="#main"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("wait_for_selector") == "#main"


async def test_language_sets_accept_language_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(language="ru"))
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("accept-language") == "ru"
    assert "language" not in req.url.params


async def test_user_agent_sets_header_not_param(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(user_agent="MyBot/2.0"))
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("user-agent") == "MyBot/2.0"
    assert "user_agent" not in req.url.params


async def test_cookies_become_cookie_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(cookies={"session": "abc"}))
    req = httpx_mock.get_requests()[0]
    assert "session=abc" in req.headers.get("cookie", "")


async def test_custom_headers_override_derived(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(language="en", custom_headers={"X-Custom": "yes", "Accept-Language": "fr"})
    await _engine().scrape("https://example.com/", opts)
    req = httpx_mock.get_requests()[0]
    assert req.headers.get("x-custom") == "yes"
    assert req.headers.get("accept-language") == "fr"


async def test_api_key_and_url_in_params(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    target = "https://target.example.com/page"
    await _engine().scrape(target)
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("api_key") == _API_KEY
    assert req.url.params.get("url") == target


async def test_extra_scraperapi_prefix_forwarded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape(
        "https://example.com/", ScrapeOptions(extra={"scraperapi_session_number": "5"})
    )
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("session_number") == "5"


def test_adapt_ignores_non_scraperapi_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    params = _adapt(opts, api_key="k", url="https://x.com")
    assert "firecrawl_foo" not in params
    assert "unrelated" not in params
