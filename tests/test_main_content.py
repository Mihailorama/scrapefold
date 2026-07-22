"""Tests for optional Trafilatura-backed main-content extraction.

Covers both layers:
- ``html_to_text.html_to_main_content`` — the extraction helper and its
  graceful ``None`` contract.
- ``ScrapeEngine.scrape`` — the central wiring that re-derives text/markdown
  from the main article body when ``opts.main_content`` is set, for any
  HTML-producing engine, and never blanks a result when extraction is
  unavailable.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_main_content
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://example.com/article"

_PAGE = """
<html><body>
  <nav>Home About Contact SUBSCRIBE NOW</nav>
  <header>Site Banner Ad</header>
  <article>
    <h1>The Real Headline</h1>
    <p>This is the substantive article paragraph with the actual content a
       reader came for, long enough to be recognised as the main body.</p>
    <p>A second real paragraph continues the article body for good measure.</p>
  </article>
  <footer>Copyright 2026 · Privacy Policy · Terms</footer>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helper-level: html_to_main_content
# ---------------------------------------------------------------------------


def test_empty_html_returns_none() -> None:
    """Empty / whitespace input returns None without needing trafilatura."""
    assert html_to_main_content("") is None
    assert html_to_main_content("   \n  ") is None


def test_extracts_main_content_drops_boilerplate() -> None:
    """With trafilatura installed, nav/header/footer boilerplate is stripped."""
    pytest.importorskip("trafilatura")

    extracted = html_to_main_content(_PAGE, base_url=_TEST_URL)
    assert extracted is not None
    text, markdown = extracted

    assert "The Real Headline" in markdown
    assert "substantive article paragraph" in text
    # Boilerplate removed
    assert "SUBSCRIBE NOW" not in text
    assert "Privacy Policy" not in text


# ---------------------------------------------------------------------------
# Base-class wiring: ScrapeEngine.scrape applies main_content centrally
# ---------------------------------------------------------------------------


class _FakeEngine(ScrapeEngine):
    """Minimal engine returning fixed full-page output for wiring tests."""

    NAME = "fake"
    CAPABILITIES = EngineCapabilities(requires_api_key=False)
    SUPPORTED_OPTIONS = frozenset({"timeout_s"})  # deliberately excludes main_content

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        return ScrapeResult(
            url=url,
            text="FULL PAGE TEXT with nav and footer",
            markdown="# FULL PAGE\n\nnav and footer",
            html="<html><body><article>x</article></body></html>",
            engine=self.NAME,
            elapsed_ms=0,
        )


async def test_base_applies_main_content_when_enabled() -> None:
    engine = _FakeEngine()

    with patch(
        "scrapefold.html_to_text.html_to_main_content",
        return_value=("MAIN ONLY", "# MAIN ONLY"),
    ) as helper:
        result = await engine.scrape(_TEST_URL, ScrapeOptions(main_content=True))

    helper.assert_called_once()
    assert result.text == "MAIN ONLY"
    assert result.markdown == "# MAIN ONLY"
    # html slot is preserved unchanged
    assert result.html is not None


async def test_base_skips_when_disabled() -> None:
    engine = _FakeEngine()

    with patch("scrapefold.html_to_text.html_to_main_content") as helper:
        result = await engine.scrape(_TEST_URL, ScrapeOptions(main_content=False))

    helper.assert_not_called()
    assert result.text == "FULL PAGE TEXT with nav and footer"


async def test_base_keeps_original_when_extraction_returns_none() -> None:
    """A None from the helper (trafilatura missing / no article) keeps output."""
    engine = _FakeEngine()

    with patch("scrapefold.html_to_text.html_to_main_content", return_value=None):
        result = await engine.scrape(_TEST_URL, ScrapeOptions(main_content=True))

    assert result.text == "FULL PAGE TEXT with nav and footer"
    assert result.markdown == "# FULL PAGE\n\nnav and footer"


async def test_base_skips_when_no_html() -> None:
    """Markdown-only engines (html=None) skip extraction entirely."""

    class _NoHtmlEngine(_FakeEngine):
        NAME = "fake_nohtml"

        async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
            return ScrapeResult(
                url=url,
                text="markdown only",
                markdown="markdown only",
                html=None,
                engine=self.NAME,
                elapsed_ms=0,
            )

    engine = _NoHtmlEngine()
    with patch("scrapefold.html_to_text.html_to_main_content") as helper:
        result = await engine.scrape(_TEST_URL, ScrapeOptions(main_content=True))

    helper.assert_not_called()
    assert result.text == "markdown only"
