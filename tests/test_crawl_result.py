"""Tests for CrawlResult dataclass (Phase G)."""

from __future__ import annotations

from pathlib import Path

import pytest

import scrapefold
from scrapefold import ScrapeOptions, ScrapeResult
from scrapefold.crawler.result import CrawlResult


def _make_result(url: str) -> ScrapeResult:
    return ScrapeResult(
        url=url,
        text=f"text-{url}",
        markdown=f"# {url}",
        html=None,
        engine="stub",
        elapsed_ms=1,
    )


# ---------------------------------------------------------------------------
# 1. CrawlResult is a frozen dataclass with slots
# ---------------------------------------------------------------------------


def test_crawl_result_is_frozen_dataclass() -> None:
    r = CrawlResult(
        pages=(_make_result("https://x.com/"),),
        stitched_path=Path("/tmp/out.md"),
        failures=(),
    )
    # frozen: cannot assign
    with pytest.raises((AttributeError, TypeError)):
        r.pages = ()  # type: ignore[misc]

    assert r.pages[0].url == "https://x.com/"
    assert r.stitched_path == Path("/tmp/out.md")
    assert r.failures == ()


# ---------------------------------------------------------------------------
# 2. crawl_site returns CrawlResult not Path
# ---------------------------------------------------------------------------


async def test_crawl_site_returns_crawl_result_not_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return ["https://example.com/"][:max_urls]

    async def _fake_scrape(
        url: str, opts: ScrapeOptions | None = None, **kw: object
    ) -> ScrapeResult:
        return ScrapeResult(
            url=url,
            text="x",
            markdown="# x",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    out = tmp_path / "out.md"
    result = await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=1),
        output=out,
    )

    assert isinstance(result, CrawlResult), f"Expected CrawlResult, got {type(result)}"
    assert result.stitched_path == out
    assert len(result.pages) == 1
