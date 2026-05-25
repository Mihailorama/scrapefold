"""Tests for the RequestsEngine — pure httpx-based HTTP GET engine.

All tests use the ``httpx_mock`` fixture from pytest-httpx so no real network
calls are made. Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError, RedirectScopeViolation
from scrapefold.engines.requests import RequestsEngine
from scrapefold.options import ScrapeOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML = "<html><head><title>Test Page</title></head><body><h1>Hello</h1><p>World</p></body></html>"

_PLAIN = "just plain text"

_JSON_OBJ = {"key": "value", "count": 42}


def _engine() -> RequestsEngine:
    return RequestsEngine()


# ---------------------------------------------------------------------------
# 1. 200 HTML response → text and markdown populated, html=raw, status_code in meta
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_html_200_populates_text_markdown_html(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/")

    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.meta["status_code"] == 200
    assert result.engine == "requests"


# ---------------------------------------------------------------------------
# 2. 200 JSON response → json populated, text = pretty JSON
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_json_200_populates_json_and_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.example.com/data",
        status_code=200,
        headers={"content-type": "application/json"},
        text=json.dumps(_JSON_OBJ),
    )
    result = await _engine().scrape("https://api.example.com/data")

    assert result.json == _JSON_OBJ
    assert result.html is None
    # text and markdown should contain the JSON content
    assert "value" in result.text
    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 3. 200 plain-text response → text populated, html=None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_plain_text_200_populates_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/robots.txt",
        status_code=200,
        headers={"content-type": "text/plain"},
        text=_PLAIN,
    )
    result = await _engine().scrape("https://example.com/robots.txt")

    assert result.text == _PLAIN
    assert result.markdown == _PLAIN
    assert result.html is None
    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 4. 404 response → result returned (no exception), status_code=404 in meta
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_404_returns_result_not_exception(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/missing",
        status_code=404,
        headers={"content-type": "text/html"},
        text="<html><body>Not Found</body></html>",
    )
    result = await _engine().scrape("https://example.com/missing")

    assert result.meta["status_code"] == 404
    assert result.engine == "requests"


# ---------------------------------------------------------------------------
# 5. 500 response → result with status_code=500 (no exception raised)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_500_returns_result_not_exception(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/error",
        status_code=500,
        headers={"content-type": "text/html"},
        text="<html><body>Internal Server Error</body></html>",
    )
    result = await _engine().scrape("https://example.com/error")

    assert result.meta["status_code"] == 500


# ---------------------------------------------------------------------------
# 6. Custom user_agent is applied
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_custom_user_agent_is_sent(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )
    opts = ScrapeOptions(user_agent="my-custom-agent/1.0")
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers["user-agent"] == "my-custom-agent/1.0"


# ---------------------------------------------------------------------------
# 7. Default user-agent is sent when none specified
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_default_user_agent_is_sent(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )
    await _engine().scrape("https://example.com/")

    request = httpx_mock.get_requests()[0]
    ua = request.headers["user-agent"]
    assert ua  # something is sent
    assert "scrapefold" in ua.lower()


# ---------------------------------------------------------------------------
# 8. language option → Accept-Language header
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_language_sets_accept_language_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )
    opts = ScrapeOptions(language="ru")
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("accept-language") == "ru"


# ---------------------------------------------------------------------------
# 9. custom_headers merge — caller-provided wins over defaults
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_custom_headers_override_defaults(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )
    opts = ScrapeOptions(custom_headers={"X-Custom": "yes", "User-Agent": "override-agent"})
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers["x-custom"] == "yes"
    assert request.headers["user-agent"] == "override-agent"


# ---------------------------------------------------------------------------
# 10. cookies passed to the request
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cookies_are_sent(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )
    opts = ScrapeOptions(cookies={"session": "abc123"})
    await _engine().scrape("https://example.com/", opts)

    request = httpx_mock.get_requests()[0]
    cookie_header = request.headers.get("cookie", "")
    assert "session=abc123" in cookie_header


# ---------------------------------------------------------------------------
# 11. Redirect is followed — final URL in result
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_redirect_is_followed(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/old",
        status_code=301,
        headers={"location": "https://example.com/new"},
        text="",
    )
    httpx_mock.add_response(
        url="https://example.com/new",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="final",
    )
    result = await _engine().scrape("https://example.com/old")

    assert result.meta["status_code"] == 200
    assert str(result.meta["final_url"]) == "https://example.com/new"


# ---------------------------------------------------------------------------
# 12. is_available() returns True (no API key needed)
# ---------------------------------------------------------------------------


def test_is_available_requires_no_api_key() -> None:
    eng = RequestsEngine()
    assert eng.is_available() is True

    eng_no_key = RequestsEngine(api_key=None)
    assert eng_no_key.is_available() is True


# ---------------------------------------------------------------------------
# 13. Unsupported options are dropped — no error, behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unsupported_options_do_not_break_the_call(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )
    # `stealth` is not in SUPPORTED_OPTIONS; the base class strips it. The
    # logging behavior itself is verified by tests/test_engine_base.py.
    result = await _engine().scrape("https://example.com/", ScrapeOptions(stealth=True))
    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 14. network error → EngineError raised (not swallowed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(
        _httpx.ConnectError("connection refused"),
        url="https://unreachable.example.com/",
    )
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://unreachable.example.com/")

    assert exc_info.value.engine == "requests"


# ---------------------------------------------------------------------------
# 15. HTML body with no content-type header is still treated as HTML
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_content_type_with_html_body_treated_as_html(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="https://example.com/no-ct",
        status_code=200,
        # no content-type header
        text=_HTML,
    )
    result = await _engine().scrape("https://example.com/no-ct")

    assert result.html == _HTML
    assert "Hello" in result.text


# ---------------------------------------------------------------------------
# 16. Binary / unknown content-type → empty text + markdown, html=None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_binary_content_type_yields_empty_text(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/image.png",
        status_code=200,
        headers={"content-type": "image/png"},
        content=b"\x89PNG\r\n",
    )
    result = await _engine().scrape("https://example.com/image.png")

    assert result.text == ""
    assert result.markdown == ""
    assert result.html is None
    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 17. same_host_redirect_scope — off-host redirect → EngineError raised
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_engine_rejects_offhost_redirect_when_scope_set(
    httpx_mock: HTTPXMock,
) -> None:
    """With same_host_redirect_scope set, an off-host 302 raises EngineError."""
    httpx_mock.add_response(
        url="https://example.com/page",
        status_code=302,
        headers={"location": "http://internal.corp/secret"},
    )
    # No mock for internal.corp — engine must abort before following the hop.

    opts = ScrapeOptions(
        extra={
            "same_host_redirect_scope": {
                "root": "https://example.com",
                "follow_subdomains": False,
            }
        }
    )
    with pytest.raises(EngineError, match="off-host"):
        await _engine().scrape("https://example.com/page", opts)


# ---------------------------------------------------------------------------
# 18. same_host_redirect_scope — same-host redirect → followed normally
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_engine_follows_same_host_redirect_when_scope_set(
    httpx_mock: HTTPXMock,
) -> None:
    """Same-host 302 is followed and the final response is returned."""
    httpx_mock.add_response(
        url="https://example.com/page",
        status_code=302,
        headers={"location": "https://example.com/new-page"},
    )
    httpx_mock.add_response(
        url="https://example.com/new-page",
        status_code=200,
        headers={"content-type": "text/html"},
        text="<html><body>hello</body></html>",
    )

    opts = ScrapeOptions(
        extra={
            "same_host_redirect_scope": {
                "root": "https://example.com",
                "follow_subdomains": False,
            }
        }
    )
    result = await _engine().scrape("https://example.com/page", opts)
    assert "hello" in result.text
    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 19. No scope → httpx default follow_redirects=True (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_engine_follows_redirects_normally_when_no_scope(
    httpx_mock: HTTPXMock,
) -> None:
    """Without a scope, existing redirect behavior is unchanged."""
    httpx_mock.add_response(
        url="https://example.com/old",
        status_code=301,
        headers={"location": "https://example.com/new"},
        text="",
    )
    httpx_mock.add_response(
        url="https://example.com/new",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="final",
    )
    result = await _engine().scrape("https://example.com/old")
    assert result.meta["status_code"] == 200
    assert str(result.meta["final_url"]) == "https://example.com/new"


# ---------------------------------------------------------------------------
# 20. same_host_redirect_scope — off-host via www-strip normalisation passes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_engine_allows_www_normalised_same_host(
    httpx_mock: HTTPXMock,
) -> None:
    """www.example.com → example.com redirect is treated as same-host."""
    httpx_mock.add_response(
        url="https://www.example.com/page",
        status_code=301,
        headers={"location": "https://example.com/page"},
    )
    httpx_mock.add_response(
        url="https://example.com/page",
        status_code=200,
        headers={"content-type": "text/plain"},
        text="ok",
    )

    opts = ScrapeOptions(
        extra={
            "same_host_redirect_scope": {
                "root": "https://www.example.com",
                "follow_subdomains": False,
            }
        }
    )
    result = await _engine().scrape("https://www.example.com/page", opts)
    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# Codex round-9 P2: malformed Location header raises RedirectScopeViolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_engine_malformed_location_raises_redirect_scope_violation(
    httpx_mock: HTTPXMock,
) -> None:
    """With same_host_redirect_scope set, a malformed Location header raises
    RedirectScopeViolation (not ValueError), so the router terminates the walk
    rather than escalating to another engine.
    """
    httpx_mock.add_response(
        url="https://example.com/page",
        status_code=302,
        headers={"location": "http://[::1"},
    )
    # No mock for the malformed target — engine must abort before following the hop.

    opts = ScrapeOptions(
        extra={
            "same_host_redirect_scope": {
                "root": "https://example.com",
                "follow_subdomains": False,
            }
        }
    )
    # Must raise RedirectScopeViolation (not ValueError) so the router
    # terminates the walk instead of escalating.
    with pytest.raises(RedirectScopeViolation):
        await _engine().scrape("https://example.com/page", opts)


# ---------------------------------------------------------------------------
# Codex round-10 P2: transient RemoteProtocolError must NOT raise RedirectScopeViolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_requests_engine_transient_protocol_error_does_not_raise_redirect_scope_violation(
    httpx_mock: HTTPXMock,
) -> None:
    """A RemoteProtocolError('Server disconnected without sending a response.')
    must NOT raise RedirectScopeViolation — it should fall through to EngineError
    so the router can escalate to the next engine rather than terminating the walk.
    """
    import httpx as _httpx

    httpx_mock.add_exception(
        _httpx.RemoteProtocolError(
            "Server disconnected without sending a response.",
            request=_httpx.Request("GET", "https://example.com/page"),
        ),
        url="https://example.com/page",
    )

    opts = ScrapeOptions(
        extra={
            "same_host_redirect_scope": {
                "root": "https://example.com",
                "follow_subdomains": False,
            }
        }
    )

    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/page", opts)

    # Must be EngineError, NOT RedirectScopeViolation
    assert type(exc_info.value) is not RedirectScopeViolation, (
        "transient RemoteProtocolError must not be treated as a scope violation"
    )
    assert exc_info.value.engine == "requests"
