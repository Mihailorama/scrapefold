"""Tests for the sync wrappers (`scrape_sync` / `crawl_site_sync`, TECH_DEBT #12).

The wrappers must survive the caller's thread already having a running (or
leaked) event loop — the Playwright-Sync-API failure mode where a bare
``asyncio.run(scrape(...))`` raises ``RuntimeError``. Offline: the walk /
crawl layers are stubbed.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

import scrapefold
from scrapefold import AllEnginesFailed, CrawlResult, ScrapeOptions, ScrapeResult


def _fake_result(url: str) -> ScrapeResult:
    return ScrapeResult(url=url, text="t", markdown="# t", html=None, engine="stub", elapsed_ms=1)


@pytest.fixture
def stub_walk(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub the router walk; records the thread each call ran on."""
    seen: dict[str, object] = {}

    async def _fake_walk(
        url: str, opts: ScrapeOptions | None = None, pool: object = None
    ) -> ScrapeResult:
        seen["url"] = url
        seen["thread"] = threading.current_thread().name
        return _fake_result(url)

    monkeypatch.setattr("scrapefold._walk", _fake_walk)
    return seen


def test_scrape_sync_plain_call(stub_walk: dict[str, object]) -> None:
    """No event loop anywhere — the wrapper just works like a blocking call."""
    result = scrapefold.scrape_sync("https://example.com")
    assert isinstance(result, ScrapeResult)
    assert result.url == "https://example.com"


def test_scrape_sync_works_inside_running_event_loop(stub_walk: dict[str, object]) -> None:
    """The named TECH_DEBT #12 acceptance test: calling scrape_sync from a
    thread whose event loop is RUNNING (the leaked-loop scenario) must return
    a ScrapeResult instead of raising RuntimeError."""

    async def harness() -> ScrapeResult:
        # We are now inside a running loop in this thread; a bare
        # asyncio.run(scrape(...)) here would raise RuntimeError.
        return scrapefold.scrape_sync("https://example.com")

    result = asyncio.run(harness())
    assert isinstance(result, ScrapeResult)
    # The walk ran on the dedicated worker thread, not the loop thread.
    assert str(stub_walk["thread"]).startswith("scrapefold-sync")


def test_scrape_sync_propagates_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failing_walk(
        url: str, opts: ScrapeOptions | None = None, pool: object = None
    ) -> ScrapeResult:
        raise AllEnginesFailed(url=url, failures=["stub:down"])

    monkeypatch.setattr("scrapefold._walk", _failing_walk)
    with pytest.raises(AllEnginesFailed):
        scrapefold.scrape_sync("https://example.com")


def test_scrape_sync_passes_opts_through(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_walk(
        url: str, opts: ScrapeOptions | None = None, pool: object = None
    ) -> ScrapeResult:
        captured["opts"] = opts
        return _fake_result(url)

    monkeypatch.setattr("scrapefold._walk", _fake_walk)
    opts = ScrapeOptions(language="ru", stealth=True)
    scrapefold.scrape_sync("https://example.com", opts)
    assert captured["opts"] is opts


def test_crawl_site_sync_works_inside_running_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_crawl_result = CrawlResult(
        pages=[_fake_result("https://example.com/")], stitched_path=tmp_path / "crawl.md"
    )

    async def _fake_crawl_site(
        url: str, opts: ScrapeOptions | None = None, output: object = None, **kw: object
    ) -> CrawlResult:
        return fake_crawl_result

    monkeypatch.setattr(scrapefold, "crawl_site", _fake_crawl_site)

    async def harness() -> CrawlResult:
        return scrapefold.crawl_site_sync("https://example.com")

    result = asyncio.run(harness())
    assert result is fake_crawl_result
