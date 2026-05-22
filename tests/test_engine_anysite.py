"""Tests for AnySiteEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.

Contract pinned here:
  - Method: POST
  - Endpoint: https://api.anysite.com/v1/scrape
  - Auth: Authorization: Bearer <api_key> header
  - Request: JSON body {"url": ..., "country": ..., "render_js": ..., "wait_ms": ..., ...}
  - Response: {"data": {"html": "...", "markdown": "...", "screenshot_b64": null},
               "meta": {"status_code": 200}}
"""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.anysite import AnySiteEngine, _adapt
from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

_ENDPOINT = "https://api.anysite.com/v1/scrape"

_HTML = (
    "<html><head><title>AnySite Test</title></head><body><h1>Hello</h1><p>World</p></body></html>"
)

_API_KEY = "test-anysite-key-123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(api_key: str = _API_KEY) -> AnySiteEngine:
    return AnySiteEngine(api_key=api_key)


def _api_response(
    *,
    html: str | None = _HTML,
    markdown: str | None = None,
    screenshot_b64: str | None = None,
    status_code: int = 200,
) -> dict:
    return {
        "data": {
            "html": html,
            "markdown": markdown,
            "screenshot_b64": screenshot_b64,
        },
        "meta": {"status_code": status_code},
    }


def _add_response(httpx_mock: HTTPXMock, **kwargs) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_ENDPOINT,
        status_code=200,
        json=_api_response(**kwargs),
    )


# ---------------------------------------------------------------------------
# 1. Basic 200 HTML in response → text, markdown, html all populated; cost=0.002
# ---------------------------------------------------------------------------


async def test_basic_200_populates_all_slots(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    result = await _engine().scrape("https://example.com/")

    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.cost_usd == 0.002
    assert result.engine == "anysite"


# ---------------------------------------------------------------------------
# 2. meta["status_code"] is set from the API response's upstream status
# ---------------------------------------------------------------------------


async def test_meta_status_code_set(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock, status_code=200)
    result = await _engine().scrape("https://example.com/")

    assert result.meta["status_code"] == 200


async def test_meta_status_code_non_200(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock, status_code=403)
    result = await _engine().scrape("https://example.com/")

    assert result.meta["status_code"] == 403


# ---------------------------------------------------------------------------
# 3. render_js=True → render_js: true in body
# ---------------------------------------------------------------------------


async def test_render_js_true_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(render_js=True)
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["render_js"] is True


# ---------------------------------------------------------------------------
# 4. render_js=False → render_js: false in body
# ---------------------------------------------------------------------------


async def test_render_js_false_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(render_js=False)
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["render_js"] is False


# ---------------------------------------------------------------------------
# 5. country="us" → country: "us" in body
# ---------------------------------------------------------------------------


async def test_country_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(country="us")
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["country"] == "us"


# ---------------------------------------------------------------------------
# 6. language="ru" → Accept-Language header on outgoing AnySite request
# ---------------------------------------------------------------------------


async def test_language_sets_accept_language_header_on_outgoing_request(
    httpx_mock: HTTPXMock,
) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(language="ru")
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    # Accept-Language is sent as a target header inside the body
    body = json.loads(request.content)
    target_headers = body.get("headers", {})
    assert target_headers.get("Accept-Language") == "ru"


# ---------------------------------------------------------------------------
# 7. stealth=True → stealth: true in body
# ---------------------------------------------------------------------------


async def test_stealth_flag_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(stealth=True)
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["stealth"] is True


# ---------------------------------------------------------------------------
# 8. premium_proxy=True → premium_proxy: true in body
# ---------------------------------------------------------------------------


async def test_premium_proxy_flag_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(premium_proxy=True)
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["premium_proxy"] is True


# ---------------------------------------------------------------------------
# 9. wait_ms=3000 → wait_ms: 3000 in body
# ---------------------------------------------------------------------------


async def test_wait_ms_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(wait_ms=3000)
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["wait_ms"] == 3000


# ---------------------------------------------------------------------------
# 10. user_agent forwarded in headers dict inside the request body (target UA)
# ---------------------------------------------------------------------------


async def test_user_agent_forwarded(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(user_agent="MyBot/3.0")
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    target_headers = body.get("headers", {})
    assert target_headers.get("User-Agent") == "MyBot/3.0"


# ---------------------------------------------------------------------------
# 11. custom_headers → carried in body["headers"] for target site
# ---------------------------------------------------------------------------


async def test_custom_headers_passed_through_to_target(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(custom_headers={"X-Custom": "yes"})
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    target_headers = body.get("headers", {})
    assert target_headers.get("X-Custom") == "yes"


# ---------------------------------------------------------------------------
# 12. cookies dict → cookies string in body
# ---------------------------------------------------------------------------


async def test_cookies_to_cookie_header_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(cookies={"session": "abc", "token": "xyz"})
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    cookie_str = body.get("cookies", "")
    assert "session=abc" in cookie_str
    assert "token=xyz" in cookie_str


# ---------------------------------------------------------------------------
# 13. extra["anysite_session"]="abc" → session: "abc" in body (prefix stripped)
# ---------------------------------------------------------------------------


async def test_anysite_extra_prefix_forwarded_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    opts = ScrapeOptions(extra={"anysite_session": "abc"})
    await _engine().scrape("https://example.com/", opts)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body.get("session") == "abc"


# ---------------------------------------------------------------------------
# 14. is_available() True with key, False without
# ---------------------------------------------------------------------------


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key() -> None:
    eng = AnySiteEngine(api_key=None)
    assert eng.is_available() is False


# ---------------------------------------------------------------------------
# 15. Network error → EngineError raised
# ---------------------------------------------------------------------------


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(
        httpx.ConnectError("connection refused"),
    )
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "anysite"


async def test_anysite_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    """AnySite-level 4xx/5xx must surface as EngineError, not silent empty result."""
    httpx_mock.add_response(method="POST", url=_ENDPOINT, status_code=401, json={})
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "anysite"


# ---------------------------------------------------------------------------
# 16. take_screenshot → screenshot_b64 populated on result
# ---------------------------------------------------------------------------


async def test_take_screenshot_sets_screenshot_b64(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock, screenshot_b64="base64encodedimage==")
    opts = ScrapeOptions(take_screenshot=True)
    result = await _engine().scrape("https://example.com/", opts)

    assert result.screenshot_b64 == "base64encodedimage=="


# ---------------------------------------------------------------------------
# 17. Unsupported options (e.g., high timeout_s) don't crash
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped(httpx_mock: HTTPXMock) -> None:
    # All options are listed as supported by anysite; test that vanilla call
    # with an unusual timeout_s still works fine.
    _add_response(httpx_mock)
    result = await _engine().scrape("https://example.com/", ScrapeOptions(timeout_s=999))
    assert result.engine == "anysite"


# ---------------------------------------------------------------------------
# 18. Authorization: Bearer header set on the outgoing request
# ---------------------------------------------------------------------------


async def test_auth_header_set(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    await _engine().scrape("https://example.com/")

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("authorization") == f"Bearer {_API_KEY}"


# ---------------------------------------------------------------------------
# 19. URL is sent in body
# ---------------------------------------------------------------------------


async def test_url_sent_in_body(httpx_mock: HTTPXMock) -> None:
    _add_response(httpx_mock)
    target = "https://target.example.com/page"
    await _engine().scrape(target)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["url"] == target


# ---------------------------------------------------------------------------
# 20. Markdown-only response (no html) → text and markdown filled from markdown
# ---------------------------------------------------------------------------


async def test_markdown_only_response_populates_text_and_markdown(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_ENDPOINT,
        status_code=200,
        json={
            "data": {
                "html": None,
                "markdown": "# Hello\n\nWorld",
                "screenshot_b64": None,
            },
            "meta": {"status_code": 200},
        },
    )
    result = await _engine().scrape("https://example.com/")

    assert result.html is None
    assert result.markdown == "# Hello\n\nWorld"
    assert "Hello" in result.text


# ---------------------------------------------------------------------------
# 21. _adapt unit test — extra key with other prefix not forwarded
# ---------------------------------------------------------------------------


def test_adapt_ignores_non_anysite_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    body = _adapt(opts, "https://x.com")
    assert "firecrawl_foo" not in body
    assert "unrelated" not in body
