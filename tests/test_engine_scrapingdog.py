"""Tests for ScrapingdogEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import logging

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.scrapingdog import ScrapingdogEngine, _adapt
from scrapefold.options import ScrapeOptions

_ENDPOINT = "https://api.scrapingdog.com/scrape"

_HTML = (
    "<html><head><title>Scrapingdog Test</title></head>"
    "<body><h1>Hello</h1><p>World</p></body></html>"
)

_API_KEY = "test-api-key-123"

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _engine(api_key: str = _API_KEY) -> ScrapingdogEngine:
    return ScrapingdogEngine(api_key=api_key)


# ---------------------------------------------------------------------------
# 1. Basic 200 HTML → html, text, markdown populated; cost_usd=0.0005
# ---------------------------------------------------------------------------


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
    assert result.cost_usd == 0.0005
    assert result.engine == "scrapingdog"


# ---------------------------------------------------------------------------
# 2. status_code stored in meta from the Scrapingdog HTTP response
# ---------------------------------------------------------------------------


async def test_meta_status_code_set(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    result = await _engine().scrape("https://example.com/")

    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 3. render_js=True → dynamic="true" in query params (string, not bool)
# ---------------------------------------------------------------------------


async def test_render_js_true_sends_dynamic_true_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(render_js=True)
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("dynamic") == "true"


# ---------------------------------------------------------------------------
# 4. render_js=False → dynamic="false" in query params (string, not bool)
# ---------------------------------------------------------------------------


async def test_render_js_false_sends_dynamic_false_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(render_js=False)
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("dynamic") == "false"


# ---------------------------------------------------------------------------
# 5. country="ru" → country=ru query param
# ---------------------------------------------------------------------------


async def test_country_maps_to_query_param(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(country="ru")
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("country") == "ru"


# ---------------------------------------------------------------------------
# 6. language="ru" → Accept-Language header (not a query param)
# ---------------------------------------------------------------------------


async def test_language_sets_accept_language_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(language="ru")
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("accept-language") == "ru"
    # Must NOT appear in query params
    assert "language" not in request.url.params


# ---------------------------------------------------------------------------
# 7. premium_proxy=True → premium="true" query param
# ---------------------------------------------------------------------------


async def test_premium_proxy_sends_premium_true_string(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(premium_proxy=True)
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("premium") == "true"


# ---------------------------------------------------------------------------
# 8. wait_ms=5000 → wait=5000 query param
# ---------------------------------------------------------------------------


async def test_wait_ms_maps_to_wait_query_param(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(wait_ms=5000)
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("wait") == "5000"


# ---------------------------------------------------------------------------
# 9. user_agent → User-Agent header (not a query param)
# ---------------------------------------------------------------------------


async def test_user_agent_sets_user_agent_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(user_agent="MyBot/2.0")
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("user-agent") == "MyBot/2.0"
    assert "user_agent" not in request.url.params


# ---------------------------------------------------------------------------
# 10. custom_headers merge correctly — custom wins, base headers preserved
# ---------------------------------------------------------------------------


async def test_custom_headers_merge(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(
        language="en",
        custom_headers={"X-Custom": "yes", "Accept-Language": "fr"},
    )
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-custom") == "yes"
    # custom_headers override derived Accept-Language
    assert request.headers.get("accept-language") == "fr"


# ---------------------------------------------------------------------------
# 11. cookies dict → Cookie header (k=v; …)
# ---------------------------------------------------------------------------


async def test_cookies_dict_becomes_cookie_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(cookies={"session": "abc", "token": "xyz"})
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    cookie_header = request.headers.get("cookie", "")
    assert "session=abc" in cookie_header
    assert "token=xyz" in cookie_header


# ---------------------------------------------------------------------------
# 12. extra["scrapingdog_session"]="abc" → session=abc in query params
# ---------------------------------------------------------------------------


async def test_extra_scrapingdog_prefix_forwarded_as_query_param(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(extra={"scrapingdog_session": "abc"})
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("session") == "abc"


# ---------------------------------------------------------------------------
# 13. is_available() True when api_key set, False when None
# ---------------------------------------------------------------------------


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key() -> None:
    eng = ScrapingdogEngine(api_key=None)
    assert eng.is_available() is False


# ---------------------------------------------------------------------------
# 14. api_key and url always appear in query params
# ---------------------------------------------------------------------------


async def test_api_key_and_url_in_query_params(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    target = "https://target.example.com/page"
    await _engine().scrape(target)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("api_key") == _API_KEY
    assert request.url.params.get("url") == target


# ---------------------------------------------------------------------------
# 15. x-request-id response header stored in meta
# ---------------------------------------------------------------------------


async def test_scrapingdog_request_id_stored_in_meta(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        headers={"x-request-id": "req-999"},
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/")

    assert result.meta.get("scrapingdog_request_id") == "req-999"


# ---------------------------------------------------------------------------
# 16. Network error → EngineError raised
# ---------------------------------------------------------------------------


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(
        _httpx.ConnectError("connection refused"),
    )
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "scrapingdog"


# ---------------------------------------------------------------------------
# 17. _adapt unit test: extra keys without scrapingdog_ prefix not forwarded
# ---------------------------------------------------------------------------


def test_adapt_ignores_non_scrapingdog_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    params = _adapt(opts, api_key="k", url="https://x.com")
    assert "firecrawl_foo" not in params
    assert "unrelated" not in params


# ---------------------------------------------------------------------------
# 18. Unsupported options silently dropped (no error), verified via caplog
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped_silently(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    opts = ScrapeOptions(stealth=True)
    with caplog.at_level(logging.DEBUG):
        result = await _engine().scrape("https://example.com/", opts)

    assert result.engine == "scrapingdog"
    dropped = [r.message for r in caplog.records if "dropping unsupported" in r.message]
    assert any("stealth" in m for m in dropped)
