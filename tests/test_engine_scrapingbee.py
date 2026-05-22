"""Tests for ScrapingbeeEngine.

All tests mock ``ScrapingBeeClient`` — no real network calls, no API key needed.
Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scrapefold.engines.base import EngineCapabilities, EngineError
from scrapefold.engines.scrapingbee import ScrapingbeeEngine, _adapt
from scrapefold.options import ScrapeOptions

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_HTML = (
    "<html><head><title>ScrapingBee Test</title></head>"
    "<body><h1>Hello</h1><p>Bees are great</p></body></html>"
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # minimal fake PNG


def _make_response(
    *,
    status_code: int = 200,
    text: str = _HTML,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a fake requests.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = content if content is not None else text.encode()
    resp.headers = headers or {"content-type": "text/html; charset=utf-8"}
    return resp


def _engine(api_key: str = "test-key-abc") -> ScrapingbeeEngine:
    return ScrapingbeeEngine(api_key=api_key)


def _patch_client(response: MagicMock) -> Any:
    """Context manager that patches ``_get_client_cls`` so no real SDK call is made."""
    mock_client_instance = MagicMock()
    mock_client_instance.get.return_value = response
    mock_client_cls = MagicMock(return_value=mock_client_instance)
    return patch(
        "scrapefold.engines.scrapingbee._get_client_cls",
        return_value=mock_client_cls,
    )


# ---------------------------------------------------------------------------
# 1. Basic HTML 200 → html, text, markdown filled; cost_usd; status_code
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_html_200_populates_all_fields() -> None:
    resp = _make_response(status_code=200, text=_HTML)
    with _patch_client(resp):
        result = await _engine().scrape("https://example.com/")

    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.cost_usd == 0.001
    assert result.meta["status_code"] == 200
    assert result.engine == "scrapingbee"


# ---------------------------------------------------------------------------
# 2. render_js=True is the default in _adapt
# ---------------------------------------------------------------------------


def test_adapt_render_js_defaults_to_true() -> None:
    opts = ScrapeOptions()  # render_js defaults to True
    params, _ = _adapt(opts)
    assert params.get("render_js") is True


# ---------------------------------------------------------------------------
# 3. render_js=False maps to render_js=False param
# ---------------------------------------------------------------------------


def test_adapt_render_js_false_forwarded() -> None:
    opts = ScrapeOptions(render_js=False)
    params, _ = _adapt(opts)
    assert params.get("render_js") is False


# ---------------------------------------------------------------------------
# 4. country → country_code
# ---------------------------------------------------------------------------


def test_adapt_country_maps_to_country_code() -> None:
    opts = ScrapeOptions(country="ru")
    params, _ = _adapt(opts)
    assert params.get("country_code") == "ru"


# ---------------------------------------------------------------------------
# 5. language → Accept-Language forwarded header
# ---------------------------------------------------------------------------


def test_adapt_language_maps_to_accept_language_header() -> None:
    opts = ScrapeOptions(language="fr")
    params, headers = _adapt(opts)
    assert headers.get("Accept-Language") == "fr"
    # forward_headers must be enabled to pass headers through
    assert params.get("forward_headers") is True


# ---------------------------------------------------------------------------
# 6. stealth=True → stealth_proxy=True
# ---------------------------------------------------------------------------


def test_adapt_stealth_maps_to_stealth_proxy() -> None:
    opts = ScrapeOptions(stealth=True)
    params, _ = _adapt(opts)
    assert params.get("stealth_proxy") is True


# ---------------------------------------------------------------------------
# 7. premium_proxy=True without stealth → premium_proxy=True
# ---------------------------------------------------------------------------


def test_adapt_premium_proxy_without_stealth() -> None:
    opts = ScrapeOptions(premium_proxy=True)
    params, _ = _adapt(opts)
    assert params.get("premium_proxy") is True
    assert not params.get("stealth_proxy")


# ---------------------------------------------------------------------------
# 8. stealth=True AND premium_proxy=True → stealth wins, warning logged
# ---------------------------------------------------------------------------


def test_adapt_stealth_wins_over_premium_proxy(caplog: pytest.LogCaptureFixture) -> None:
    opts = ScrapeOptions(stealth=True, premium_proxy=True)
    with caplog.at_level(logging.WARNING, logger="scrapefold.engines.scrapingbee"):
        params, _ = _adapt(opts)

    assert params.get("stealth_proxy") is True
    assert not params.get("premium_proxy")
    warnings = [r.message for r in caplog.records if "premium_proxy" in r.message.lower()]
    assert warnings, "expected a warning about premium_proxy being overridden by stealth"


# ---------------------------------------------------------------------------
# 9. wait_ms → wait
# ---------------------------------------------------------------------------


def test_adapt_wait_ms_maps_to_wait() -> None:
    opts = ScrapeOptions(wait_ms=8000)
    params, _ = _adapt(opts)
    assert params.get("wait") == 8000


# ---------------------------------------------------------------------------
# 10. wait_for_selector → wait_for
# ---------------------------------------------------------------------------


def test_adapt_wait_for_selector_maps_to_wait_for() -> None:
    opts = ScrapeOptions(wait_for_selector=".my-class")
    params, _ = _adapt(opts)
    assert params.get("wait_for") == ".my-class"


# ---------------------------------------------------------------------------
# 11. cookies dict → semicolon-joined cookie string param
# ---------------------------------------------------------------------------


def test_adapt_cookies_dict_joined_as_string() -> None:
    opts = ScrapeOptions(cookies={"session": "abc123", "lang": "en"})
    params, _ = _adapt(opts)
    cookie_str = params.get("cookies")
    assert cookie_str is not None
    assert "session=abc123" in cookie_str
    assert "lang=en" in cookie_str


# ---------------------------------------------------------------------------
# 12. take_screenshot=True → screenshot_b64 populated, html/text/markdown empty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_screenshot_populates_b64_and_clears_html_text() -> None:
    resp = _make_response(
        status_code=200,
        content=_PNG_BYTES,
        text="",
        headers={"content-type": "image/png"},
    )
    opts = ScrapeOptions(take_screenshot=True)
    with _patch_client(resp):
        result = await _engine().scrape("https://example.com/", opts)

    assert result.screenshot_b64 == base64.b64encode(_PNG_BYTES).decode()
    assert result.html is None
    assert result.text == ""
    assert result.markdown == ""


# ---------------------------------------------------------------------------
# 13. extra["scrapingbee_window_width"]=1920 → window_width=1920 in params
# ---------------------------------------------------------------------------


def test_adapt_extra_scrapingbee_prefix_stripped_and_forwarded() -> None:
    opts = ScrapeOptions(extra={"scrapingbee_window_width": 1920, "scrapingbee_block_ads": True})
    params, _ = _adapt(opts)
    assert params.get("window_width") == 1920
    assert params.get("block_ads") is True


# ---------------------------------------------------------------------------
# 14. is_available() True when api_key set, False when missing
# ---------------------------------------------------------------------------


def test_is_available_with_and_without_key() -> None:
    eng_with = ScrapingbeeEngine(api_key="some-key")
    assert eng_with.is_available() is True

    eng_without = ScrapingbeeEngine(api_key=None)
    assert eng_without.is_available() is False


# ---------------------------------------------------------------------------
# 15. CAPABILITIES shape sanity-check
# ---------------------------------------------------------------------------


def test_capabilities_declared_correctly() -> None:
    caps: EngineCapabilities = ScrapingbeeEngine.CAPABILITIES
    assert caps.js_rendering is True
    assert caps.stealth is True
    assert caps.screenshot is True
    assert caps.requires_api_key is True
    assert caps.proxy_type == "residential"
    assert caps.estimated_cost_usd == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# 16. Spb-Resolved-Url response header lands in meta
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_spb_resolved_url_in_meta() -> None:
    resp = _make_response(
        headers={
            "content-type": "text/html; charset=utf-8",
            "Spb-Resolved-Url": "https://example.com/final",
        }
    )
    with _patch_client(resp):
        result = await _engine().scrape("https://example.com/redirect")

    assert result.meta.get("spb_resolved_url") == "https://example.com/final"


# ---------------------------------------------------------------------------
# 17. SDK exception propagates as EngineError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sdk_exception_raises_engine_error() -> None:
    mock_client_instance = MagicMock()
    mock_client_instance.get.side_effect = RuntimeError("quota exceeded")
    mock_client_cls = MagicMock(return_value=mock_client_instance)

    with (
        patch(
            "scrapefold.engines.scrapingbee._get_client_cls",
            return_value=mock_client_cls,
        ),
        pytest.raises(EngineError) as exc_info,
    ):
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "scrapingbee"
    assert "quota exceeded" in exc_info.value.message


# ---------------------------------------------------------------------------
# 18. user_agent → User-Agent forwarded header
# ---------------------------------------------------------------------------


def test_adapt_user_agent_forwarded_in_headers() -> None:
    opts = ScrapeOptions(user_agent="MyBot/2.0")
    params, headers = _adapt(opts)
    assert headers.get("User-Agent") == "MyBot/2.0"
    assert params.get("forward_headers") is True
