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
    # Default opts have render_js=True → 10 credits even without sa-credit-cost header
    assert result.cost_usd == 10 * 0.00049
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


async def test_output_format_markdown_sets_native_markdown(httpx_mock: HTTPXMock) -> None:
    md = "# Title\n\nbody text"
    httpx_mock.add_response(status_code=200, headers={"content-type": "text/markdown"}, text=md)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(output_format="markdown"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("output_format") == "markdown"
    assert result.markdown == md  # not re-derived from HTML
    assert result.html is None


async def test_autoparse_json_fills_json_slot(httpx_mock: HTTPXMock) -> None:
    payload = {"name": "Widget", "price": 9.99}
    httpx_mock.add_response(
        status_code=200,
        headers={"content-type": "application/json"},
        json=payload,
    )
    opts = ScrapeOptions(extra={"scraperapi_autoparse": "true"})
    result = await _engine().scrape("https://example.com/", opts)
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("autoparse") == "true"
    assert result.json == payload
    assert "Widget" in result.markdown  # best-effort pretty JSON


async def test_json_content_type_without_autoparse_still_parsed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200, headers={"content-type": "application/json"}, json={"a": 1}
    )
    result = await _engine().scrape("https://example.com/")
    assert result.json == {"a": 1}


async def test_credit_cost_header_stored_in_meta(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, headers={"sa-credit-cost": "10"}, text=_HTML)
    result = await _engine().scrape("https://example.com/")
    assert result.meta.get("scraperapi_credit_cost") == "10"


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAPERAPI_API_KEY", raising=False)
    assert ScraperApiEngine(api_key=None).is_available() is False


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")
    assert exc_info.value.engine == "scraperapi"


async def test_unsupported_wait_ms_dropped_not_forwarded(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    # wait_ms is NOT in SUPPORTED_OPTIONS — base class strips it, no "wait" param.
    await _engine().scrape("https://example.com/", ScrapeOptions(wait_ms=9000))
    req = httpx_mock.get_requests()[0]
    assert "wait" not in req.url.params
    assert "wait_ms" not in req.url.params


async def test_unsupported_options_do_not_break_call(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=200, text=_HTML)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(stealth=True))
    assert result.engine == "scraperapi"


# ---------------------------------------------------------------------------
# Fix 1 — Canonical target status_code
# ---------------------------------------------------------------------------


async def test_sa_statuscode_header_becomes_canonical_status(httpx_mock: HTTPXMock) -> None:
    """sa-statuscode: 403 on a 200 API response → result.status_code == 403."""
    httpx_mock.add_response(
        status_code=200,
        headers={"sa-statuscode": "403"},
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/")
    assert result.status_code == 403
    assert result.meta["scraperapi_api_status"] == 200
    assert result.meta["scraperapi_target_status"] == "403"


async def test_no_sa_statuscode_keeps_api_status(httpx_mock: HTTPXMock) -> None:
    """When sa-statuscode is absent, status_code == API HTTP status."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    result = await _engine().scrape("https://example.com/")
    assert result.status_code == 200
    assert "scraperapi_api_status" not in result.meta


# ---------------------------------------------------------------------------
# Fix 2 — Report actual credit cost
# ---------------------------------------------------------------------------


async def test_credit_cost_header_sets_cost_usd(httpx_mock: HTTPXMock) -> None:
    """sa-credit-cost: 10 header → cost_usd == 10 * 0.00049."""
    httpx_mock.add_response(
        status_code=200,
        headers={"sa-credit-cost": "10"},
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/")
    assert result.cost_usd == 10 * 0.00049


async def test_no_header_render_js_true_costs_10_credits(httpx_mock: HTTPXMock) -> None:
    """No sa-credit-cost header + render_js=True → cost = 10 * 0.00049."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(render_js=True))
    assert result.cost_usd == 10 * 0.00049


async def test_no_header_render_js_false_no_premium_costs_1_credit(
    httpx_mock: HTTPXMock,
) -> None:
    """No sa-credit-cost header, render_js=False, no premium → cost = 1 * 0.00049."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(render_js=False))
    assert result.cost_usd == 0.00049


# ---------------------------------------------------------------------------
# Fix 3 — keep_headers forwarded when caller headers are set
# ---------------------------------------------------------------------------


async def test_keep_headers_set_when_user_agent_present(httpx_mock: HTTPXMock) -> None:
    """ScrapeOptions(user_agent=...) → keep_headers=true in query params."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(user_agent="TestBot/1.0"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("keep_headers") == "true"


async def test_keep_headers_not_set_when_no_caller_headers(httpx_mock: HTTPXMock) -> None:
    """Plain ScrapeOptions() → keep_headers NOT in query params."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions())
    req = httpx_mock.get_requests()[0]
    assert "keep_headers" not in req.url.params


async def test_keep_headers_set_when_language_present(httpx_mock: HTTPXMock) -> None:
    """ScrapeOptions(language=...) → keep_headers=true."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(language="fr"))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("keep_headers") == "true"


async def test_keep_headers_set_when_cookies_present(httpx_mock: HTTPXMock) -> None:
    """ScrapeOptions(cookies=...) → keep_headers=true."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(cookies={"s": "abc"}))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("keep_headers") == "true"


async def test_keep_headers_set_when_custom_headers_present(httpx_mock: HTTPXMock) -> None:
    """ScrapeOptions(custom_headers=...) → keep_headers=true."""
    httpx_mock.add_response(status_code=200, text=_HTML)
    await _engine().scrape("https://example.com/", ScrapeOptions(custom_headers={"X-My": "val"}))
    req = httpx_mock.get_requests()[0]
    assert req.url.params.get("keep_headers") == "true"


# ---------------------------------------------------------------------------
# Fix 4 — text slot must be plain text for native markdown
# ---------------------------------------------------------------------------


async def test_output_format_markdown_text_is_plain_text(httpx_mock: HTTPXMock) -> None:
    """Native markdown: result.markdown keeps raw md; result.text has no '# ' markers."""
    md = "# Title\n\nbody text"
    httpx_mock.add_response(status_code=200, headers={"content-type": "text/markdown"}, text=md)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(output_format="markdown"))
    assert result.markdown == md
    assert "# " not in result.text
    assert "Title" in result.text


def test_registered_in_registry() -> None:
    from scrapefold.engines import get_engine

    cls = get_engine("scraperapi")
    assert cls is ScraperApiEngine
