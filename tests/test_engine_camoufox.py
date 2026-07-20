"""Tests for the CamoufoxEngine.

All tests run offline — no real browser, no camoufox SDK required. The SDK's
AsyncCamoufox is monkeypatched with a mock whose async context manager yields a
mock Playwright Browser.

camoufox 0.5.x flow:
    async with AsyncCamoufox(**launch) as browser:   # yields a Playwright Browser
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url, timeout=<ms>, wait_until=...)
        html = await page.content()
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.camoufox import CamoufoxEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(
    *,
    html: str = "<h1>Hello</h1><p>World</p>",
    screenshot: bytes = b"\x89PNG-bytes",
) -> MagicMock:
    page = MagicMock()
    page.set_extra_http_headers = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.content = AsyncMock(return_value=html)
    page.screenshot = AsyncMock(return_value=screenshot)
    return page


def _make_camoufox(page: MagicMock | None = None) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (AsyncCamoufox class mock, context mock, page mock)."""
    if page is None:
        page = _make_page()
    context = MagicMock()
    context.add_cookies = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=browser)
    instance.__aexit__ = AsyncMock(return_value=False)
    camoufox_class = MagicMock(return_value=instance)
    return camoufox_class, context, page


# ---------------------------------------------------------------------------
# 1. Basic fetch success
# ---------------------------------------------------------------------------


async def test_basic_fetch_success() -> None:
    camoufox, _, _ = _make_camoufox(_make_page(html="<h1>Hi</h1><p>Content</p>"))

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.engine == "camoufox"
    assert result.cost_usd == 0.0
    assert result.text
    assert result.markdown
    assert result.html
    # headless + humanize are always on
    launch = camoufox.call_args.kwargs
    assert launch["headless"] is True
    assert launch["humanize"] is True


# ---------------------------------------------------------------------------
# 2. language → locale launch option + Accept-Language header
# ---------------------------------------------------------------------------


async def test_language_maps_to_locale_and_header() -> None:
    camoufox, _, page = _make_camoufox()

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(language="ru"))

    assert camoufox.call_args.kwargs.get("locale") == "ru"
    page.set_extra_http_headers.assert_awaited_once()
    headers = page.set_extra_http_headers.await_args.args[0]
    assert headers.get("Accept-Language") == "ru"


# ---------------------------------------------------------------------------
# 3. cookies added to the context, scoped to the target URL
# ---------------------------------------------------------------------------


async def test_cookies_scoped_to_target_url() -> None:
    camoufox, context, _ = _make_camoufox()

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(cookies={"session": "abc123"}))

    context.add_cookies.assert_awaited_once()
    passed = context.add_cookies.await_args.args[0]
    assert {"name": "session", "value": "abc123", "url": _TEST_URL} in passed


# ---------------------------------------------------------------------------
# 4. custom_headers forwarded via set_extra_http_headers
# ---------------------------------------------------------------------------


async def test_custom_headers_forwarded() -> None:
    camoufox, _, page = _make_camoufox()

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(custom_headers={"X-My-Header": "val"}))

    page.set_extra_http_headers.assert_awaited_once()
    headers = page.set_extra_http_headers.await_args.args[0]
    assert headers.get("X-My-Header") == "val"


# ---------------------------------------------------------------------------
# 5. wait_until + timeout forwarded to page.goto (timeout in ms)
# ---------------------------------------------------------------------------


async def test_wait_until_and_timeout_forwarded() -> None:
    camoufox, _, page = _make_camoufox()

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        await engine.scrape(
            _TEST_URL,
            ScrapeOptions(wait_until="networkidle", timeout_s=45),
        )

    page.goto.assert_awaited_once()
    assert page.goto.await_args.args[0] == _TEST_URL
    assert page.goto.await_args.kwargs.get("wait_until") == "networkidle"
    assert page.goto.await_args.kwargs.get("timeout") == 45 * 1000


# ---------------------------------------------------------------------------
# 6. wait_for_selector honored
# ---------------------------------------------------------------------------


async def test_wait_for_selector_honored() -> None:
    camoufox, _, page = _make_camoufox()

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(wait_for_selector=".content"))

    page.wait_for_selector.assert_awaited_once()
    assert page.wait_for_selector.await_args.args[0] == ".content"


# ---------------------------------------------------------------------------
# 7. take_screenshot → base64 of the PNG bytes
# ---------------------------------------------------------------------------


async def test_take_screenshot_base64() -> None:
    png = b"\x89PNG-fake-bytes"
    camoufox, _, page = _make_camoufox(_make_page(screenshot=png))

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        result = await engine.scrape(_TEST_URL, ScrapeOptions(take_screenshot=True))

    page.screenshot.assert_awaited_once()
    assert result.screenshot_b64 == base64.b64encode(png).decode("ascii")


# ---------------------------------------------------------------------------
# 8. extra camoufox_* passthrough into launch options
# ---------------------------------------------------------------------------


async def test_extra_camoufox_passthrough() -> None:
    camoufox, _, _ = _make_camoufox()
    proxy = {"server": "http://127.0.0.1:8080"}

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(extra={"camoufox_proxy": proxy}))

    assert camoufox.call_args.kwargs.get("proxy") == proxy


# ---------------------------------------------------------------------------
# 9. user_agent is NOT forwarded (dropped to keep fingerprint coherent)
# ---------------------------------------------------------------------------


async def test_user_agent_dropped() -> None:
    camoufox, _, page = _make_camoufox()

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        result = await engine.scrape(_TEST_URL, ScrapeOptions(user_agent="MyBot/1.0"))

    # No exception, and UA never leaks into launch options or headers.
    assert isinstance(result, ScrapeResult)
    assert "user_agent" not in camoufox.call_args.kwargs
    if page.set_extra_http_headers.await_args is not None:
        headers = page.set_extra_http_headers.await_args.args[0]
        assert "User-Agent" not in headers


# ---------------------------------------------------------------------------
# 10. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    browser = MagicMock()
    browser.new_context = AsyncMock(side_effect=RuntimeError("firefox crashed"))
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=browser)
    instance.__aexit__ = AsyncMock(return_value=False)
    camoufox = MagicMock(return_value=instance)

    with patch("scrapefold.engines.camoufox.AsyncCamoufox", camoufox):
        engine = CamoufoxEngine()
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "camoufox"
    assert "firefox crashed" in exc_info.value.message


# ---------------------------------------------------------------------------
# 11. is_available — no API key required
# ---------------------------------------------------------------------------


def test_is_available_true() -> None:
    assert CamoufoxEngine().is_available() is True


# ---------------------------------------------------------------------------
# Unified proxy → Camoufox Playwright proxy dict (the rotation pool's exit)
# ---------------------------------------------------------------------------


def test_unified_proxy_maps_to_playwright_proxy_dict() -> None:
    from scrapefold.engines.camoufox import _adapt_launch

    kwargs = _adapt_launch(ScrapeOptions(proxy="http://user:pass@host:8000"))
    assert kwargs["proxy"] == {"server": "http://user:pass@host:8000"}


def test_explicit_camoufox_proxy_overrides_unified_proxy() -> None:
    from scrapefold.engines.camoufox import _adapt_launch

    explicit = {"server": "http://ex:9000", "username": "u", "password": "p"}
    kwargs = _adapt_launch(
        ScrapeOptions(proxy="http://host:8000", extra={"camoufox_proxy": explicit})
    )
    assert kwargs["proxy"] == explicit


def test_camoufox_proxy_in_supported_options() -> None:
    assert "proxy" in CamoufoxEngine.SUPPORTED_OPTIONS
