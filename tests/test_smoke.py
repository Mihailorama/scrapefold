"""Smoke tests that verify the scaffold imports and the public types
behave like dataclasses. Each subsequent PR (S2+) adds real behavior tests."""

from __future__ import annotations

import pytest

import scrapefold
from scrapefold import EngineCapabilities, ScrapeEngine, ScrapeOptions, ScrapeResult
from scrapefold.engines import list_engine_names


def test_version_string() -> None:
    assert scrapefold.__version__.startswith("0.1.0")


def test_options_defaults() -> None:
    opts = ScrapeOptions()
    assert opts.render_js is True
    assert opts.wait_ms == 5000
    assert opts.language is None
    assert opts.engines is None
    assert opts.extra == {}


def test_options_with_updates_is_frozen_safe() -> None:
    opts = ScrapeOptions(language="ru")
    updated = opts.with_updates(country="ru", render_js=False)
    # original untouched
    assert opts.country is None
    assert opts.render_js is True
    # new copy has the updates
    assert updated.language == "ru"
    assert updated.country == "ru"
    assert updated.render_js is False


def test_result_construction() -> None:
    res = ScrapeResult(
        url="https://example.com",
        text="hello",
        markdown="# hello",
        html=None,
        engine="stub",
        elapsed_ms=42,
    )
    assert res.cost_usd == 0.0
    assert res.json is None
    assert res.is_empty() is False
    assert ScrapeResult(
        url="x", text="", markdown="", html=None, engine="s", elapsed_ms=0
    ).is_empty()


def test_result_supports_all_four_formats() -> None:
    """text, markdown, html, json — every engine result is reachable via get_format."""
    res = ScrapeResult(
        url="https://example.com",
        text="plain",
        markdown="# plain",
        html="<h1>plain</h1>",
        engine="stub",
        elapsed_ms=1,
        json={"title": "plain", "count": 1},
    )
    assert res.get_format("text") == "plain"
    assert res.get_format("markdown") == "# plain"
    assert res.get_format("html") == "<h1>plain</h1>"
    assert res.get_format("json") == {"title": "plain", "count": 1}
    with pytest.raises(ValueError):
        res.get_format("yaml")


def test_result_is_empty_recognizes_json_only() -> None:
    """An engine that only returned JSON is not 'empty'."""
    res = ScrapeResult(
        url="x",
        text="",
        markdown="",
        html=None,
        engine="s",
        elapsed_ms=0,
        json={"k": "v"},
    )
    assert res.is_empty() is False


def test_options_output_format_accepts_json() -> None:
    """ScrapeOptions.output_format must allow the four user-facing formats."""
    for fmt in ("text", "markdown", "html", "json", "auto"):
        opts = ScrapeOptions(output_format=fmt)  # type: ignore[arg-type]
        assert opts.output_format == fmt


def test_capabilities_defaults() -> None:
    cap = EngineCapabilities()
    assert cap.js_rendering is False
    assert cap.requires_api_key is True
    assert cap.deprecated is False


def test_scrape_engine_is_abstract() -> None:
    """ScrapeEngine cannot be instantiated without implementing _fetch."""
    with pytest.raises(TypeError):
        ScrapeEngine()  # type: ignore[abstract]


def test_engine_registry_is_empty_in_scaffold() -> None:
    """S1 scaffold registers no engines. Real engines land in S2-S6, S11."""
    assert list_engine_names() == []


async def test_public_scrape_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await scrapefold.scrape("https://example.com")


async def test_public_crawl_site_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await scrapefold.crawl_site("https://example.com")
