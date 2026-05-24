"""End-to-end tests for scrapefold.crawl_site (Pack 4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import scrapefold
from scrapefold import ScrapeOptions, ScrapeResult


@pytest.fixture
def stub_scrape(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub scrapefold.scrape so crawl_site tests are offline."""
    called: list[str] = []

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        called.append(url)
        return ScrapeResult(
            url=url,
            text=f"text-of-{url}",
            markdown=f"# {url}",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    return {"called": called}


@pytest.fixture
def stub_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_discover(root: str, *, max_urls: int) -> list[str]:
        return [
            "https://example.com/",
            "https://example.com/a",
            "https://example.com/b",
        ][:max_urls]

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)


async def test_crawl_site_writes_stitched_file(
    tmp_path: Path,
    stub_discover: None,
    stub_scrape: dict[str, Any],
) -> None:
    out = tmp_path / "crawl.md"
    result_path = await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=3),
        output=out,
    )

    assert result_path == out
    text = out.read_text()
    assert "https://example.com/" in text
    assert "https://example.com/a" in text
    assert "https://example.com/b" in text
    assert len(stub_scrape["called"]) == 3


async def test_crawl_site_honors_max_pages(
    tmp_path: Path,
    stub_discover: None,
    stub_scrape: dict[str, Any],
) -> None:
    out = tmp_path / "crawl.md"
    await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=2),
        output=out,
    )
    assert len(stub_scrape["called"]) == 2


async def test_crawl_site_default_output_under_tmp(
    stub_discover: None, stub_scrape: dict[str, Any]
) -> None:
    # No output= given → crawl_site picks a sensible default
    result_path = await scrapefold.crawl_site(
        "https://example.com/", opts=ScrapeOptions(max_pages=1)
    )
    assert result_path.exists()
    result_path.unlink()  # cleanup


async def test_crawl_site_accepts_and_ignores_unimplemented_kwargs(
    tmp_path: Path,
    stub_discover: None,
    stub_scrape: dict[str, Any],
) -> None:
    """README-documented kwargs (cache_dir, cache_ttl_hours) MUST NOT raise TypeError."""
    out = tmp_path / "x.md"
    # These kwargs are Pack 5 scope — for now they must be silently ignored.
    result_path = await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=1),
        output=out,
        cache_dir=tmp_path / "cache",
        cache_ttl_hours=24,
    )
    assert result_path == out
