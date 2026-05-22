"""Tests for CloakBrowserEngine.

All tests run offline — no real network calls, no real Playwright/browser
required. The cloakbrowser SDK's ``launch_context_async`` is monkeypatched
with AsyncMock/MagicMock throughout.

Pinned contract (cloakbrowser 0.3.28):
    ctx = await cloakbrowser.launch_context_async(
        headless=True,
        proxy=None | str | ProxySettings,
        user_agent=None | str,
        locale=None | str,
        stealth_args=True,
        humanize=False,
        extra_http_headers={...},   # via **kwargs → new_context()
        **extra_kwargs,
    )
    page = await ctx.new_page()
    await page.goto(url, wait_until="load", timeout=<ms>)
    html = await page.content()
    screenshot_bytes = await page.screenshot(type="png")   # when requested
    await ctx.close()

ProxySettings(server=<url>) — TypedDict subclassing dict; pass server as key.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.cloakbrowser import CloakBrowserEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://example.com"
_SAMPLE_HTML = "<html><body><h1>Hello</h1><p>World</p></body></html>"
_FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page_mock(
    html: str = _SAMPLE_HTML,
    screenshot_bytes: bytes = _FAKE_PNG,
) -> AsyncMock:
    """Return an async mock of a Playwright Page."""
    page = MagicMock()
    page.goto = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value=html)
    page.screenshot = AsyncMock(return_value=screenshot_bytes)
    return page


def _make_context_mock(page: AsyncMock | None = None) -> AsyncMock:
    """Return an async mock of a Playwright BrowserContext."""
    if page is None:
        page = _make_page_mock()
    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)
    ctx.close = AsyncMock()
    return ctx


def _make_launch_mock(ctx: AsyncMock | None = None) -> AsyncMock:
    """Return an AsyncMock for cloakbrowser.launch_context_async."""
    if ctx is None:
        ctx = _make_context_mock()
    return AsyncMock(return_value=ctx)


def _launch_kwargs(launch_mock: AsyncMock) -> dict:
    """Return kwargs from the most recent launch_context_async call."""
    return dict(launch_mock.call_args.kwargs)


# ---------------------------------------------------------------------------
# 1. Basic fetch success
# ---------------------------------------------------------------------------


async def test_basic_fetch_success() -> None:
    """Engine fetches a URL, returns ScrapeResult with text + markdown + html."""
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.engine == "cloakbrowser"
    assert result.html == _SAMPLE_HTML
    assert result.text  # must be non-empty
    assert result.markdown  # must be non-empty
    assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# 2. user_agent forwarded
# ---------------------------------------------------------------------------


async def test_user_agent_forwarded() -> None:
    """opts.user_agent is passed as ``user_agent`` to launch_context_async."""
    launch = _make_launch_mock()
    ua = "Mozilla/5.0 (custom)"

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(user_agent=ua))

    assert _launch_kwargs(launch).get("user_agent") == ua


# ---------------------------------------------------------------------------
# 3. cookies forwarded via extra_http_headers
# ---------------------------------------------------------------------------


async def test_cookies_forwarded() -> None:
    """opts.cookies are serialised and forwarded as 'Cookie' header."""
    launch = _make_launch_mock()
    cookies = {"session": "abc", "lang": "en"}

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(cookies=cookies))

    kwargs = _launch_kwargs(launch)
    extra_headers = kwargs.get("extra_http_headers", {})
    cookie_header = extra_headers.get("Cookie", "")
    # Both key=value pairs must appear in the Cookie header
    assert "session=abc" in cookie_header
    assert "lang=en" in cookie_header


# ---------------------------------------------------------------------------
# 4. custom_headers forwarded via extra_http_headers
# ---------------------------------------------------------------------------


async def test_custom_headers_forwarded() -> None:
    """opts.custom_headers are forwarded as extra_http_headers."""
    launch = _make_launch_mock()
    headers = {"X-Custom-Token": "tok123", "Accept": "text/html"}

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(custom_headers=headers))

    kwargs = _launch_kwargs(launch)
    extra_headers = kwargs.get("extra_http_headers", {})
    assert extra_headers.get("X-Custom-Token") == "tok123"
    assert extra_headers.get("Accept") == "text/html"


# ---------------------------------------------------------------------------
# 5. stealth flag — stealth_args always True (cloakbrowser is a stealth browser)
# ---------------------------------------------------------------------------


async def test_stealth_flag_forwarded() -> None:
    """stealth_args is always True regardless of opts.stealth value.

    cloakbrowser is inherently a stealth browser; stealth_args disabling
    its fingerprint hardening would be counterproductive, so the engine
    always passes stealth_args=True.
    """
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        # Even with stealth=False, we keep stealth_args=True
        await engine.scrape(_TEST_URL, ScrapeOptions(stealth=False))

    assert _launch_kwargs(launch).get("stealth_args") is True


# ---------------------------------------------------------------------------
# 6. premium_proxy forwarded via proxy kwarg
# ---------------------------------------------------------------------------


async def test_premium_proxy_alone_is_dropped() -> None:
    """``premium_proxy=True`` alone has nothing to point at — cloakbrowser runs
    locally and has no built-in residential pool. The engine drops the flag
    silently and lets callers supply a real URL via ``extra["cloakbrowser_proxy"]``.
    """
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(premium_proxy=True, country="us"))

    kwargs = _launch_kwargs(launch)
    assert "proxy" not in kwargs, "premium_proxy alone should not synthesize a fake URL"


async def test_explicit_cloakbrowser_proxy_extra_forwarded() -> None:
    """When callers supply a concrete proxy URL via ``extra``, it reaches the SDK."""
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        await engine.scrape(
            _TEST_URL,
            ScrapeOptions(extra={"cloakbrowser_proxy": "http://proxy.example.com:8080"}),
        )

    kwargs = _launch_kwargs(launch)
    assert kwargs.get("proxy") == "http://proxy.example.com:8080"


# ---------------------------------------------------------------------------
# 7. take_screenshot → screenshot_b64 populated
# ---------------------------------------------------------------------------


async def test_take_screenshot_sets_screenshot_b64() -> None:
    """When opts.take_screenshot=True, result.screenshot_b64 is a non-empty string."""
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        result = await engine.scrape(_TEST_URL, ScrapeOptions(take_screenshot=True))

    assert result.screenshot_b64 is not None
    assert isinstance(result.screenshot_b64, str)
    assert len(result.screenshot_b64) > 0
    # Must be valid base64
    base64.b64decode(result.screenshot_b64, validate=True)


# ---------------------------------------------------------------------------
# 8. unsupported options dropped (not passed to SDK)
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped() -> None:
    """Options not in SUPPORTED_OPTIONS are stripped before _fetch is called.

    The base class handles stripping; here we verify that engine-irrelevant
    options (e.g. include_links, max_pages which are crawl-scope opts) do not
    leak into SDK kwargs.
    """
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        # include_links and max_pages are NOT in SUPPORTED_OPTIONS
        await engine.scrape(
            _TEST_URL,
            ScrapeOptions(include_links=False, max_pages=5),
        )

    kwargs = _launch_kwargs(launch)
    # Neither include_links nor max_pages should appear in SDK call kwargs
    assert "include_links" not in kwargs
    assert "max_pages" not in kwargs


# ---------------------------------------------------------------------------
# 9. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    """Any exception from launch_context_async or page ops is wrapped in EngineError."""
    launch = AsyncMock(side_effect=RuntimeError("browser crashed"))

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "cloakbrowser"
    assert "browser crashed" in exc_info.value.message


# ---------------------------------------------------------------------------
# 10. is_available — no API key required
# ---------------------------------------------------------------------------


def test_is_available_true_no_key_required() -> None:
    """CloakBrowserEngine requires no API key, so is_available() is always True."""
    engine = CloakBrowserEngine()
    assert engine.is_available() is True


# ---------------------------------------------------------------------------
# 11. language → locale kwarg
# ---------------------------------------------------------------------------


async def test_language_maps_to_locale() -> None:
    """opts.language is forwarded as ``locale`` to launch_context_async."""
    launch = _make_launch_mock()

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(language="ru"))

    assert _launch_kwargs(launch).get("locale") == "ru"


# ---------------------------------------------------------------------------
# 12. context is closed even on page.goto failure (cleanup guard)
# ---------------------------------------------------------------------------


async def test_context_closed_on_page_error() -> None:
    """BrowserContext.close() is called even when page.goto raises."""
    page = _make_page_mock()
    page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_CONNECTION_REFUSED"))
    ctx = _make_context_mock(page=page)
    launch = _make_launch_mock(ctx=ctx)

    with patch("scrapefold.engines.cloakbrowser.launch_context_async", launch):
        engine = CloakBrowserEngine()
        with pytest.raises(EngineError):
            await engine.scrape(_TEST_URL)

    ctx.close.assert_called_once()


# ---------------------------------------------------------------------------
# 13. Regression guard — kwargs match SDK signature (skipped if not installed)
# ---------------------------------------------------------------------------


def test_regression_kwargs_match_sdk_signature() -> None:
    """Verify the kwargs used in _fetch are valid launch_context_async params.

    Skipped automatically when cloakbrowser is not installed.
    Contract pinned against cloakbrowser 0.3.28.
    """
    cloakbrowser = pytest.importorskip("cloakbrowser")
    import inspect

    sig = inspect.signature(cloakbrowser.launch_context_async)
    valid_params = set(sig.parameters.keys())

    # These are the kwargs the engine unconditionally passes
    engine_required_kwargs = {"headless", "stealth_args", "humanize"}
    # These are conditional kwargs that may also be passed
    engine_optional_kwargs = {"proxy", "user_agent", "locale", "extra_http_headers"}

    # All required kwargs must be valid params or accepted via **kwargs
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    for kwarg in engine_required_kwargs:
        assert kwarg in valid_params or has_var_keyword, (
            f"Required engine kwarg {kwarg!r} is not a valid "
            f"launch_context_async param. Valid params: {sorted(valid_params)}"
        )

    for kwarg in engine_optional_kwargs:
        assert kwarg in valid_params or has_var_keyword, (
            f"Optional engine kwarg {kwarg!r} is not a valid "
            f"launch_context_async param. Valid params: {sorted(valid_params)}"
        )
