"""Tests for the FirecrawlEngine.

All tests run offline — no real network calls, no real API key required.
The SDK's AsyncFirecrawlApp is monkeypatched with an AsyncMock.

The engine MUST call ``app.scrape(url, **sdk_kwargs)`` (top-level snake_case
kwargs that match ``firecrawl.v2.types.ScrapeOptions``). It must NOT wrap
options under ``params=`` — the v2 SDK silently ignores that, billing the
caller for default scrapes while dropping every option.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.firecrawl import FirecrawlEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://example.com"


def _make_sdk_document(**kwargs) -> MagicMock:
    """Return a minimal mock of the SDK's Document object."""
    doc = MagicMock()
    doc.markdown = kwargs.get("markdown", "# Hello\n\nWorld")
    doc.html = kwargs.get("html", "<h1>Hello</h1><p>World</p>")
    doc.screenshot = kwargs.get("screenshot")
    doc.json = kwargs.get("json")
    raw_meta = kwargs.get("meta", {"status_code": 200, "title": "Example"})
    meta_mock = MagicMock()
    meta_mock.model_dump = MagicMock(return_value=raw_meta)
    doc.metadata = meta_mock
    doc.metadata_dict = raw_meta
    return doc


def _make_app_mock(scrape_result=None, extract_result=None) -> MagicMock:
    app_instance = MagicMock()
    app_instance.scrape = AsyncMock(return_value=scrape_result or _make_sdk_document())
    app_instance.extract = AsyncMock(
        return_value=extract_result or MagicMock(data=[{"name": "Acme"}])
    )
    return MagicMock(return_value=app_instance)


def _scrape_kwargs(app_class: MagicMock) -> dict:
    """Return the kwargs passed to the most recent ``app.scrape`` call."""
    call = app_class.return_value.scrape.call_args
    return dict(call.kwargs)


# ---------------------------------------------------------------------------
# 1. Basic /scrape success
# ---------------------------------------------------------------------------


async def test_basic_scrape_success() -> None:
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
    assert result.text
    assert result.cost_usd == 0.001
    assert result.engine == "firecrawl"
    assert result.meta.get("status_code") == 200


# ---------------------------------------------------------------------------
# 2. Engine never wraps options under ``params=`` (regression guard for the
#    P1 Codex finding — v2 SDK silently drops the unknown ``params`` kwarg).
# ---------------------------------------------------------------------------


async def test_engine_does_not_pass_params_wrapper() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(stealth=True, country="ru"))

    kwargs = _scrape_kwargs(app_class)
    assert "params" not in kwargs, (
        "Engine wrapped options under params= — firecrawl-py v2 ignores that, "
        "drop the wrapper and pass kwargs at the top level."
    )


# ---------------------------------------------------------------------------
# 3. render_js=False → formats=["markdown"]
# ---------------------------------------------------------------------------


async def test_render_js_false_passes_markdown_only_format() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(render_js=False))

    kwargs = _scrape_kwargs(app_class)
    formats = kwargs.get("formats", [])
    assert "markdown" in formats
    assert "html" not in formats


# ---------------------------------------------------------------------------
# 4. language → Accept-Language in headers
# ---------------------------------------------------------------------------


async def test_language_maps_to_accept_language_header() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(language="ru"))

    kwargs = _scrape_kwargs(app_class)
    assert kwargs.get("headers", {}).get("Accept-Language") == "ru"


# ---------------------------------------------------------------------------
# 5. country → location={"country": ...}
# ---------------------------------------------------------------------------


async def test_country_maps_to_location_country() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(country="ru"))

    kwargs = _scrape_kwargs(app_class)
    assert kwargs.get("location") == {"country": "ru"}


# ---------------------------------------------------------------------------
# 6. stealth=True → proxy="stealth"
# ---------------------------------------------------------------------------


async def test_stealth_true_maps_to_proxy_stealth() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(stealth=True))

    assert _scrape_kwargs(app_class).get("proxy") == "stealth"


# ---------------------------------------------------------------------------
# 7. premium_proxy=True (no stealth) → proxy="enhanced"
#    v2 SDK Literal is ('basic'|'stealth'|'enhanced'|'auto'); "premium"
#    is not a valid value — closest semantic match is "enhanced".
# ---------------------------------------------------------------------------


async def test_premium_proxy_without_stealth_maps_to_proxy_enhanced() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(premium_proxy=True, stealth=False))

    assert _scrape_kwargs(app_class).get("proxy") == "enhanced"


# ---------------------------------------------------------------------------
# 8. wait_ms → wait_for (snake_case int)
# ---------------------------------------------------------------------------


async def test_wait_ms_maps_to_wait_for_int() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(wait_ms=8000))

    assert _scrape_kwargs(app_class).get("wait_for") == 8000


# ---------------------------------------------------------------------------
# 9. wait_for_selector → actions=[{"type":"wait","selector":...}]
#    v2 SDK ``wait_for`` is int-only, so CSS-selector waits go via actions.
# ---------------------------------------------------------------------------


async def test_wait_for_selector_maps_to_wait_action() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(wait_for_selector=".foo"))

    kwargs = _scrape_kwargs(app_class)
    actions = kwargs.get("actions", [])
    assert any(a.get("type") == "wait" and a.get("selector") == ".foo" for a in actions), (
        f"expected a wait-selector action, got actions={actions!r}"
    )
    # And the int wait_for must NOT carry a stringy selector.
    assert not isinstance(kwargs.get("wait_for"), str)


# ---------------------------------------------------------------------------
# 10. take_screenshot=True → "screenshot" in formats; screenshot_b64 set
# ---------------------------------------------------------------------------


async def test_take_screenshot_adds_screenshot_format_and_fills_result() -> None:
    doc = _make_sdk_document(screenshot="data:image/png;base64,abc123")
    app_class = _make_app_mock(scrape_result=doc)
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        result = await engine.scrape(_TEST_URL, ScrapeOptions(take_screenshot=True))

    assert "screenshot" in _scrape_kwargs(app_class).get("formats", [])
    assert result.screenshot_b64 == "data:image/png;base64,abc123"


# ---------------------------------------------------------------------------
# 11. output_format="json" + schema → extract path
# ---------------------------------------------------------------------------


async def test_output_format_json_with_schema_uses_extract_path() -> None:
    extract_mock_result = MagicMock()
    extract_mock_result.data = [{"name": "Acme Corp", "founded": 1999}]
    app_class = _make_app_mock(extract_result=extract_mock_result)

    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        result = await engine.scrape(
            _TEST_URL,
            ScrapeOptions(output_format="json", extra={"schema": schema}),
        )

    app_instance = app_class.return_value
    app_instance.extract.assert_called_once()
    app_instance.scrape.assert_not_called()
    assert result.json == [{"name": "Acme Corp", "founded": 1999}]


# ---------------------------------------------------------------------------
# 12. output_format="html" → formats=["html"]
# ---------------------------------------------------------------------------


async def test_output_format_html_passes_html_format() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(_TEST_URL, ScrapeOptions(output_format="html"))

    assert _scrape_kwargs(app_class).get("formats") == ["html"]


# ---------------------------------------------------------------------------
# 13. timeout_s → timeout in ms (top-level kwarg)
# ---------------------------------------------------------------------------


async def test_timeout_s_maps_to_timeout_ms() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        # ScrapeOptions default timeout_s is 30; pick a non-default value.
        await engine.scrape(_TEST_URL, ScrapeOptions(timeout_s=45))

    assert _scrape_kwargs(app_class).get("timeout") == 45_000


# ---------------------------------------------------------------------------
# 14. is_available()
# ---------------------------------------------------------------------------


def test_is_available_true_when_api_key_set() -> None:
    assert FirecrawlEngine(api_key="fc-test-key").is_available() is True


def test_is_available_false_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert FirecrawlEngine(api_key=None).is_available() is False


# ---------------------------------------------------------------------------
# 15. Engine wraps SDK exception in EngineError
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
# 16. firecrawl_* extra keys forwarded as top-level kwargs
# ---------------------------------------------------------------------------


async def test_firecrawl_prefixed_extra_keys_are_passed_through() -> None:
    app_class = _make_app_mock()
    with patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", app_class):
        engine = FirecrawlEngine(api_key="fc-test")
        await engine.scrape(
            _TEST_URL,
            ScrapeOptions(extra={"firecrawl_only_main_content": True}),
        )

    assert _scrape_kwargs(app_class).get("only_main_content") is True
