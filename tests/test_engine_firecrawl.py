"""Tests for the FirecrawlEngine.

All tests run offline — no real network calls, no real API key required.
The SDK's AsyncFirecrawlApp is monkeypatched with an AsyncMock.

Test inventory (14 tests):
 1. Basic /scrape success: markdown + html + text populated, cost_usd=0.001, meta filled
 2. render_js=False → formats=["markdown"] param passed (best-effort no-browser hint)
 3. language → Accept-Language in headers param
 4. country → location.country param
 5. stealth=True → proxy="stealth"
 6. premium_proxy=True (no stealth) → proxy="premium"
 7. wait_ms → waitFor (int)
 8. wait_for_selector → waitFor (string selector)
 9. take_screenshot=True → "screenshot" in formats and screenshot_b64 in result
10. output_format="json" + extra["schema"] → extract path, json populated
11. output_format="html" → formats=["html"]
12. is_available() True when api_key set, False when not
13. Engine wraps SDK exception in EngineError (via base class)
14. firecrawl_* extra keys passthrough
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.firecrawl import FirecrawlEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_URL = "https://example.com"


def _make_sdk_document(**kwargs) -> MagicMock:
    """Return a minimal mock of the SDK's Document object."""
    doc = MagicMock()
    doc.markdown = kwargs.get("markdown", "# Hello\n\nWorld")
    doc.html = kwargs.get("html", "<h1>Hello</h1><p>World</p>")
    doc.screenshot = kwargs.get("screenshot")
    doc.json = kwargs.get("json")
    raw_meta = kwargs.get("meta", {"status_code": 200, "title": "Example"})
    # Simulate metadata_dict property
    meta_mock = MagicMock()
    meta_mock.model_dump = MagicMock(return_value=raw_meta)
    doc.metadata = meta_mock
    doc.metadata_dict = raw_meta
    return doc


def _make_app_mock(scrape_result=None, extract_result=None) -> MagicMock:
    """Build a mock AsyncFirecrawlApp (class mock) returning given results."""
    app_instance = MagicMock()
    app_instance.scrape = AsyncMock(return_value=scrape_result or _make_sdk_document())
    app_instance.extract = AsyncMock(
        return_value=extract_result or MagicMock(data=[{"name": "Acme"}])
    )
    # The class itself, when called, returns the instance
    app_class = MagicMock(return_value=app_instance)
    return app_class


# ---------------------------------------------------------------------------
# 1. Basic /scrape success
# ---------------------------------------------------------------------------


async def test_basic_scrape_success() -> None:
    """Successful /scrape populates markdown, html, text, cost_usd, and meta."""
    doc = _make_sdk_document(
        markdown="# Hi\n\nContent",
        html="<h1>Hi</h1><p>Content</p>",
        meta={"status_code": 200, "title": "Hi"},
    )
    app_class = _make_app_mock(scrape_result=doc)

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.markdown == "# Hi\n\nContent"
    assert result.html == "<h1>Hi</h1><p>Content</p>"
    assert result.text  # non-empty derived text
    assert result.cost_usd == 0.001
    assert result.engine == "firecrawl"
    assert result.meta.get("status_code") == 200
    assert result.meta.get("title") == "Hi"


# ---------------------------------------------------------------------------
# 2. render_js=False
# ---------------------------------------------------------------------------


async def test_render_js_false_passes_markdown_only_format() -> None:
    """render_js=False passes formats=["markdown"] as a best-effort hint."""
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(render_js=False)
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    # The params dict is passed as a keyword argument
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    formats = params.get("formats", [])
    assert "markdown" in formats
    # When render_js=False there is no "html" in formats
    assert "html" not in formats


# ---------------------------------------------------------------------------
# 3. language → Accept-Language header
# ---------------------------------------------------------------------------


async def test_language_maps_to_accept_language_header() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(language="ru")
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    headers = params.get("headers", {})
    assert headers.get("Accept-Language") == "ru"


# ---------------------------------------------------------------------------
# 4. country → location.country
# ---------------------------------------------------------------------------


async def test_country_maps_to_location_country() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(country="ru")
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    location = params.get("location", {})
    assert location.get("country") == "ru"


# ---------------------------------------------------------------------------
# 5. stealth=True → proxy="stealth"
# ---------------------------------------------------------------------------


async def test_stealth_true_maps_to_proxy_stealth() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(stealth=True)
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert params.get("proxy") == "stealth"


# ---------------------------------------------------------------------------
# 6. premium_proxy=True (no stealth) → proxy="premium"
# ---------------------------------------------------------------------------


async def test_premium_proxy_without_stealth_maps_to_proxy_premium() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(premium_proxy=True, stealth=False)
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert params.get("proxy") == "premium"


# ---------------------------------------------------------------------------
# 7. wait_ms → waitFor (int)
# ---------------------------------------------------------------------------


async def test_wait_ms_maps_to_wait_for_int() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(wait_ms=8000)
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert params.get("waitFor") == 8000


# ---------------------------------------------------------------------------
# 8. wait_for_selector → waitFor (string)
# ---------------------------------------------------------------------------


async def test_wait_for_selector_maps_to_wait_for_string() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(wait_for_selector=".foo")
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert params.get("waitFor") == ".foo"


# ---------------------------------------------------------------------------
# 9. take_screenshot=True → screenshot in formats + screenshot_b64 in result
# ---------------------------------------------------------------------------


async def test_take_screenshot_adds_screenshot_format_and_fills_result() -> None:
    doc = _make_sdk_document(screenshot="data:image/png;base64,abc123")
    app_class = _make_app_mock(scrape_result=doc)

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(take_screenshot=True)
        result = await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert "screenshot" in params.get("formats", [])
    assert result.screenshot_b64 == "data:image/png;base64,abc123"


# ---------------------------------------------------------------------------
# 10. output_format="json" + extra["schema"] → extract path, json populated
# ---------------------------------------------------------------------------


async def test_output_format_json_with_schema_uses_extract_path() -> None:
    extract_mock_result = MagicMock()
    extract_mock_result.data = [{"name": "Acme Corp", "founded": 1999}]
    app_class = _make_app_mock(extract_result=extract_mock_result)

    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(output_format="json", extra={"schema": schema})
        result = await engine.scrape(_TEST_URL, opts)

    # extract() should have been called, not scrape()
    app_instance = app_class.return_value
    app_instance.extract.assert_called_once()
    app_instance.scrape.assert_not_called()
    assert result.json == [{"name": "Acme Corp", "founded": 1999}]


# ---------------------------------------------------------------------------
# 11. output_format="html" → formats=["html"]
# ---------------------------------------------------------------------------


async def test_output_format_html_passes_html_format() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(output_format="html")
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert params.get("formats") == ["html"]


# ---------------------------------------------------------------------------
# 12. is_available()
# ---------------------------------------------------------------------------


def test_is_available_true_when_api_key_set() -> None:
    engine = FirecrawlEngine(api_key="fc-test-key")
    assert engine.is_available() is True


def test_is_available_false_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    engine = FirecrawlEngine(api_key=None)
    assert engine.is_available() is False


# ---------------------------------------------------------------------------
# 13. Engine wraps SDK exception in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    app_instance = MagicMock()
    app_instance.scrape = AsyncMock(side_effect=RuntimeError("quota exceeded"))
    app_class = MagicMock(return_value=app_instance)

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "firecrawl"
    assert "quota exceeded" in exc_info.value.message


# ---------------------------------------------------------------------------
# 14. firecrawl_* extra keys passthrough
# ---------------------------------------------------------------------------


async def test_firecrawl_prefixed_extra_keys_are_passed_through() -> None:
    app_class = _make_app_mock()

    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        opts = ScrapeOptions(extra={"firecrawl_replaceAllPathsWithAbsolutePaths": True})
        await engine.scrape(_TEST_URL, opts)

    call_kwargs = app_class.return_value.scrape.call_args
    params = call_kwargs.kwargs.get("params") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert params.get("replaceAllPathsWithAbsolutePaths") is True
