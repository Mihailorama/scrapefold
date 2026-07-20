"""Tests for ScraplingStealthEngine.

All tests mock ``StealthyFetcher`` — no real network calls, no browser needed.
Follows the offline-by-default golden rule.

SDK signatures pinned here (test 7 regression guard):
  StealthyFetcher.fetch(url: str, **kwargs: Unpack[StealthSession]) -> Response
  Key StealthSession kwargs: useragent, extra_headers, cookies, timeout, wait,
      wait_selector, proxy
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from conftest import make_scrapling_response
from scrapefold.engines.base import EngineCapabilities, EngineError
from scrapefold.engines.scrapling_stealth import ScraplingStealthEngine
from scrapefold.options import ScrapeOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML = (
    "<html><head><title>Stealth Test</title></head>"
    "<body><h1>Hello Stealth</h1><p>works</p></body></html>"
)


def _make_response(html: str = _HTML, status: int = 200) -> MagicMock:
    return make_scrapling_response(html, status=status)


def _engine() -> ScraplingStealthEngine:
    return ScraplingStealthEngine()


def _patch_fetcher(response: MagicMock) -> Any:
    """Patch StealthyFetcher at the engine module level so to_thread receives a mock."""
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=response)
    return patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls)


# ---------------------------------------------------------------------------
# 1. Basic fetch success — all format slots populated, cost=0.0, engine name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_basic_fetch_success() -> None:
    resp = _make_response()
    with _patch_fetcher(resp):
        result = await _engine().scrape("https://example.com/")

    assert result.engine == "scrapling_stealth"
    assert result.html == _HTML
    assert "Hello Stealth" in result.text
    assert "Hello Stealth" in result.markdown
    assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# 2. user_agent → useragent kwarg forwarded to StealthyFetcher.fetch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_user_agent_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls):
        opts = ScrapeOptions(user_agent="MyBot/1.0")
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.fetch.call_args[1]
    assert call_kwargs.get("useragent") == "MyBot/1.0"


# ---------------------------------------------------------------------------
# 3. cookies dict forwarded to StealthyFetcher.fetch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cookies_converted_to_playwright_format() -> None:
    """Cookies must reach Playwright as a list of objects scoped to the URL,
    not as a raw dict (Playwright's add_cookies rejects raw dicts)."""
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=resp)
    target = "https://example.com/dashboard"

    with patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls):
        opts = ScrapeOptions(cookies={"session": "abc", "lang": "en"})
        await _engine().scrape(target, opts)

    cookies = mock_cls.fetch.call_args[1].get("cookies")
    assert isinstance(cookies, list)
    assert {"name": "session", "value": "abc", "url": target} in cookies
    assert {"name": "lang", "value": "en", "url": target} in cookies


# ---------------------------------------------------------------------------
# 4. custom_headers forwarded via extra_headers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_custom_headers_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls):
        opts = ScrapeOptions(custom_headers={"X-Token": "my-token"})
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.fetch.call_args[1]
    extra_headers = call_kwargs.get("extra_headers") or {}
    assert extra_headers.get("X-Token") == "my-token"


# ---------------------------------------------------------------------------
# 5. Unsupported option (country) does NOT crash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unsupported_options_dropped() -> None:
    resp = _make_response()
    with _patch_fetcher(resp):
        opts = ScrapeOptions(country="ru")
        # Must not raise — country is silently dropped
        result = await _engine().scrape("https://example.com/", opts)

    assert result.engine == "scrapling_stealth"


# ---------------------------------------------------------------------------
# 6. SDK exception → EngineError with correct engine name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sdk_exception_wrapped_in_engine_error() -> None:
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(side_effect=RuntimeError("browser crashed"))

    with (
        patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls),
        pytest.raises(EngineError) as exc_info,
    ):
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "scrapling_stealth"
    assert "browser crashed" in exc_info.value.message


# ---------------------------------------------------------------------------
# 7. Regression guard — pin the exact kwargs the engine passes to scrapling
#    Uses pytest.importorskip so it is SKIPPED when scrapling is not installed.
# ---------------------------------------------------------------------------


def test_regression_kwargs_match_sdk_signature() -> None:
    scrapling = pytest.importorskip("scrapling")
    import inspect

    StealthyFetcher = scrapling.StealthyFetcher  # noqa: N806 (SDK class name)
    sig = inspect.signature(StealthyFetcher.fetch)
    # fetch accepts **kwargs: Unpack[StealthSession] — parameter is **kwargs
    param_names = list(sig.parameters.keys())
    assert "url" in param_names
    assert "kwargs" in param_names

    # Pin the StealthSession fields our engine uses
    from scrapling.engines._browsers._types import StealthSession

    annotations = getattr(StealthSession, "__annotations__", {})
    for field in ("useragent", "extra_headers", "cookies", "timeout", "wait", "wait_selector"):
        assert field in annotations, f"StealthSession missing field {field!r}"


# ---------------------------------------------------------------------------
# 8. is_available() is always True (no API key required)
# ---------------------------------------------------------------------------


def test_is_available_true() -> None:
    eng = ScraplingStealthEngine()
    assert eng.is_available() is True

    eng_no_key = ScraplingStealthEngine(api_key=None)
    assert eng_no_key.is_available() is True


# ---------------------------------------------------------------------------
# 9. wait_ms forwarded as `wait` kwarg
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_ms_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls):
        opts = ScrapeOptions(wait_ms=3000)
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.fetch.call_args[1]
    assert call_kwargs.get("wait") == 3000


@pytest.mark.anyio
async def test_timeout_forwarded_even_at_default() -> None:
    """Regression: scrapling's 30s default must not silently override scrapefold's
    60s when the caller uses ``ScrapeOptions()`` defaults — timeout is ALWAYS
    forwarded as ms.
    """
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls):
        await _engine().scrape("https://example.com/", ScrapeOptions())

    assert mock_cls.fetch.call_args[1].get("timeout") == ScrapeOptions().timeout_s * 1000


# ---------------------------------------------------------------------------
# 10. wait_for_selector forwarded as `wait_selector`
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_selector_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.fetch = MagicMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls):
        opts = ScrapeOptions(wait_for_selector=".content")
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.fetch.call_args[1]
    assert call_kwargs.get("wait_selector") == ".content"


# ---------------------------------------------------------------------------
# 11. CAPABILITIES: js_rendering=True, stealth=True, requires_api_key=False
# ---------------------------------------------------------------------------


def test_capabilities_declared_correctly() -> None:
    caps: EngineCapabilities = ScraplingStealthEngine.CAPABILITIES
    assert caps.js_rendering is True
    assert caps.stealth is True
    assert caps.requires_api_key is False
    assert caps.estimated_cost_usd == pytest.approx(0.0)
    assert caps.default_timeout_s == 60


# ---------------------------------------------------------------------------
# Unified proxy → scrapling StealthSession proxy kwarg (rotation pool exit)
# ---------------------------------------------------------------------------


def test_unified_proxy_maps_to_scrapling_proxy_kwarg() -> None:
    from scrapefold.engines.scrapling_stealth import _adapt

    kwargs = _adapt(ScrapeOptions(proxy="http://user:pass@host:8000"), "https://example.com/")
    assert kwargs["proxy"] == "http://user:pass@host:8000"


def test_scrapling_proxy_in_supported_options() -> None:
    assert "proxy" in ScraplingStealthEngine.SUPPORTED_OPTIONS
