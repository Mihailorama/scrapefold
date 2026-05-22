"""Tests for ScraplingFastEngine.

All tests mock ``AsyncFetcher`` — no real network calls needed.
Follows the offline-by-default golden rule.

SDK signatures pinned here (test 7 regression guard):
  AsyncFetcher.get(url: str, **kwargs: Unpack[GetRequestParams]) -> Awaitable[Response]
  Key GetRequestParams kwargs: headers, cookies, timeout, proxies, proxy
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineCapabilities, EngineError
from scrapefold.engines.scrapling_fast import ScraplingFastEngine
from scrapefold.options import ScrapeOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML = (
    "<html><head><title>Fast Test</title></head>"
    "<body><h1>Hello Fast</h1><p>fast path</p></body></html>"
)


def _make_response(
    html: str = _HTML,
    status: int = 200,
) -> MagicMock:
    """Build a fake scrapling Response-like mock."""
    resp = MagicMock()
    resp.status = status
    resp.html_content = MagicMock()
    resp.html_content.__str__ = lambda self: html
    return resp


def _engine() -> ScraplingFastEngine:
    return ScraplingFastEngine()


def _patch_fetcher(response: MagicMock) -> Any:
    """Patch AsyncFetcher at the engine module level."""
    mock_cls = MagicMock()
    mock_cls.get = AsyncMock(return_value=response)
    return patch("scrapefold.engines.scrapling_fast.AsyncFetcher", mock_cls)


# ---------------------------------------------------------------------------
# 1. Basic fetch success — all format slots populated, cost=0.0, engine name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_basic_fetch_success() -> None:
    resp = _make_response()
    with _patch_fetcher(resp):
        result = await _engine().scrape("https://example.com/")

    assert result.engine == "scrapling_fast"
    assert result.html == _HTML
    assert "Hello Fast" in result.text
    assert "Hello Fast" in result.markdown
    assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# 2. user_agent forwarded via headers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_user_agent_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.get = AsyncMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_fast.AsyncFetcher", mock_cls):
        opts = ScrapeOptions(user_agent="SpeedBot/3.0")
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.get.call_args[1]
    headers = call_kwargs.get("headers") or {}
    assert headers.get("User-Agent") == "SpeedBot/3.0"


# ---------------------------------------------------------------------------
# 3. cookies dict forwarded via cookies kwarg
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cookies_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.get = AsyncMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_fast.AsyncFetcher", mock_cls):
        opts = ScrapeOptions(cookies={"tok": "xyz"})
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.get.call_args[1]
    # AsyncFetcher.get accepts cookies= directly
    assert "cookies" in call_kwargs


# ---------------------------------------------------------------------------
# 4. custom_headers forwarded via headers kwarg
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_custom_headers_forwarded() -> None:
    resp = _make_response()
    mock_cls = MagicMock()
    mock_cls.get = AsyncMock(return_value=resp)

    with patch("scrapefold.engines.scrapling_fast.AsyncFetcher", mock_cls):
        opts = ScrapeOptions(custom_headers={"Authorization": "Bearer tok"})
        await _engine().scrape("https://example.com/", opts)

    call_kwargs = mock_cls.get.call_args[1]
    headers = call_kwargs.get("headers") or {}
    assert headers.get("Authorization") == "Bearer tok"


# ---------------------------------------------------------------------------
# 5. Unsupported option (country) does NOT crash
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unsupported_options_dropped() -> None:
    resp = _make_response()
    with _patch_fetcher(resp):
        opts = ScrapeOptions(country="ru")
        result = await _engine().scrape("https://example.com/", opts)

    assert result.engine == "scrapling_fast"


# ---------------------------------------------------------------------------
# 6. SDK exception → EngineError with correct engine name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sdk_exception_wrapped_in_engine_error() -> None:
    mock_cls = MagicMock()
    mock_cls.get = AsyncMock(side_effect=RuntimeError("connection refused"))

    with (
        patch("scrapefold.engines.scrapling_fast.AsyncFetcher", mock_cls),
        pytest.raises(EngineError) as exc_info,
    ):
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "scrapling_fast"
    assert "connection refused" in exc_info.value.message


# ---------------------------------------------------------------------------
# 7. Regression guard — pin exact kwargs the engine passes to AsyncFetcher.get
#    Uses pytest.importorskip so it is SKIPPED when scrapling is not installed.
# ---------------------------------------------------------------------------


def test_regression_kwargs_match_sdk_signature() -> None:
    pytest.importorskip("scrapling")
    import inspect

    from scrapling.fetchers import AsyncFetcher

    sig = inspect.signature(AsyncFetcher.get)
    param_names = list(sig.parameters.keys())
    assert "url" in param_names
    assert "kwargs" in param_names

    # Pin the GetRequestParams fields our engine uses
    from scrapling.engines._browsers._types import GetRequestParams

    annotations = getattr(GetRequestParams, "__annotations__", {})
    for field in ("headers", "cookies", "timeout"):
        assert field in annotations, f"GetRequestParams missing field {field!r}"


# ---------------------------------------------------------------------------
# 8. is_available() always True (no API key required)
# ---------------------------------------------------------------------------


def test_is_available_true() -> None:
    eng = ScraplingFastEngine()
    assert eng.is_available() is True

    eng_no_key = ScraplingFastEngine(api_key=None)
    assert eng_no_key.is_available() is True


# ---------------------------------------------------------------------------
# 9. CAPABILITIES: js_rendering=False, stealth=False
# ---------------------------------------------------------------------------


def test_no_js_rendering_capability() -> None:
    caps: EngineCapabilities = ScraplingFastEngine.CAPABILITIES
    assert caps.js_rendering is False
    assert caps.stealth is False
    assert caps.requires_api_key is False
    assert caps.estimated_cost_usd == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 10. Fast engine default_timeout_s is lower than stealth engine
# ---------------------------------------------------------------------------


def test_fast_default_timeout_lower_than_stealth() -> None:
    from scrapefold.engines.scrapling_stealth import ScraplingStealthEngine

    assert (
        ScraplingFastEngine.CAPABILITIES.default_timeout_s
        < ScraplingStealthEngine.CAPABILITIES.default_timeout_s
    )
