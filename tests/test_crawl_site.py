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
    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
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
    # No output= given → crawl_site picks a unique temp file
    result_path = await scrapefold.crawl_site(
        "https://example.com/", opts=ScrapeOptions(max_pages=1)
    )
    assert result_path.exists()
    assert result_path.name.startswith("scrapefold-crawl-")
    assert result_path.suffix == ".md"
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


# ---------------------------------------------------------------------------
# Finding 3 (Codex P2 round-3): max_pages=0 / negative returns empty crawl
# ---------------------------------------------------------------------------


async def test_crawl_site_zero_max_pages_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.max_pages=0 returns a Path to an empty file; discover_urls is not called."""

    async def _fail_discover(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError("discover_urls must not be called when max_pages=0")

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fail_discover)

    out = tmp_path / "zero.md"
    result_path = await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=0),
        output=out,
    )
    assert result_path == out
    assert out.read_text() == ""


async def test_crawl_site_negative_max_pages_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.max_pages=-1 returns a Path to an empty file; discover_urls is not called."""

    async def _fail_discover(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError("discover_urls must not be called when max_pages<0")

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fail_discover)

    out = tmp_path / "neg.md"
    result_path = await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=-1),
        output=out,
    )
    assert result_path == out
    assert out.read_text() == ""


# ---------------------------------------------------------------------------
# Finding 2 (Codex P2 round-4): unique default output paths
# ---------------------------------------------------------------------------


async def test_crawl_site_default_output_paths_are_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two successive default crawls produce two distinct output files."""

    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return ["https://example.com/"][:max_urls]

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        return ScrapeResult(
            url=url,
            text=f"text-{url}",
            markdown=f"# {url}",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    result_a = await scrapefold.crawl_site("https://example.com/", opts=ScrapeOptions(max_pages=1))
    result_b = await scrapefold.crawl_site("https://example.com/", opts=ScrapeOptions(max_pages=1))

    assert result_a != result_b, "default output paths must be unique across crawls"
    assert result_a.name.startswith("scrapefold-crawl-")
    assert result_b.name.startswith("scrapefold-crawl-")

    # cleanup
    for p in (result_a, result_b):
        if p.exists():
            p.unlink()


# ---------------------------------------------------------------------------
# Finding 1 (Codex P1 round-4): pre-flight HEAD rejects scrape-time off-host redirects
# ---------------------------------------------------------------------------


async def test_crawl_site_skips_url_that_redirects_off_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A discovered URL that 302s off-host MUST NOT be scraped (SSRF guard)."""
    import httpx

    scraped_urls: list[str] = []

    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return ["https://example.com/redirector", "https://example.com/safe"][:max_urls]

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        scraped_urls.append(url)
        return ScrapeResult(
            url=url,
            text=f"text-{url}",
            markdown=f"# {url}",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    # HEAD responses: redirector → 302 off-host; safe → 200
    async def _fake_head(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: object,  # type: ignore[override]
    ) -> httpx.Response:
        if "redirector" in url:
            return httpx.Response(
                302,
                headers={"location": "http://internal.corp/secret"},
                request=httpx.Request("HEAD", url),
            )
        return httpx.Response(200, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)

    out = tmp_path / "out.md"
    await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=5),
        output=out,
    )

    assert "https://example.com/redirector" not in scraped_urls, (
        "off-host redirect URL must not be scraped"
    )
    assert "https://example.com/safe" in scraped_urls, "non-redirecting URL must still be scraped"


async def test_crawl_site_injects_redirect_scope_into_per_url_opts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """crawl_site MUST inject same_host_redirect_scope into opts.extra for each scrape call."""
    import httpx

    captured: list[tuple[str, object]] = []

    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return ["https://example.com/a", "https://example.com/b"][:max_urls]

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        scope = (opts.extra or {}).get("same_host_redirect_scope")
        captured.append((url, scope))
        return ScrapeResult(
            url=url,
            text=f"text-{url}",
            markdown=f"# {url}",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    async def _fake_head(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        return httpx.Response(200, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)

    out = tmp_path / "out.md"
    await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=5),
        output=out,
    )

    assert len(captured) == 2, "both discovered URLs should be scraped"
    for url, scope in captured:
        assert scope is not None, f"same_host_redirect_scope must be set for {url}"
        assert isinstance(scope, dict)
        assert scope["root"] == "https://example.com/", (
            f"scope root must equal the crawl root, got {scope['root']!r}"
        )
        assert "follow_subdomains" in scope


async def test_crawl_site_preflight_200_proceeds_to_scrape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL returning 200 on HEAD must proceed to scrape (no false-positive block)."""
    import httpx

    scraped_urls: list[str] = []

    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return ["https://example.com/page"][:max_urls]

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        scraped_urls.append(url)
        return ScrapeResult(
            url=url,
            text="content",
            markdown="# content",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    async def _fake_head(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: object,  # type: ignore[override]
    ) -> httpx.Response:
        return httpx.Response(200, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)

    out = tmp_path / "out.md"
    await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=1),
        output=out,
    )

    assert "https://example.com/page" in scraped_urls, "200 HEAD response must not block the scrape"


# ---------------------------------------------------------------------------
# Codex round-9 P2: preflight HEAD — malformed Location header must not raise ValueError
# ---------------------------------------------------------------------------


async def test_crawl_site_preflight_skips_url_with_malformed_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 302 with a malformed Location header in HEAD MUST NOT raise ValueError.

    The URL should be skipped (treated as unsafe), not abort the crawl.
    """
    import httpx

    scraped_urls: list[str] = []

    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return ["https://example.com/bad-redirect", "https://example.com/safe"][:max_urls]

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        scraped_urls.append(url)
        return ScrapeResult(
            url=url,
            text=f"text-{url}",
            markdown=f"# {url}",
            html=None,
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    async def _fake_head(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        if "bad-redirect" in url:
            # 302 with a malformed Location header (unterminated IPv6 bracket)
            return httpx.Response(
                302,
                headers={"location": "http://[::1"},
                request=httpx.Request("HEAD", url),
            )
        return httpx.Response(200, request=httpx.Request("HEAD", url))

    monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)

    out = tmp_path / "out.md"
    # Must not raise ValueError; malformed Location causes URL to be skipped.
    await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=5),
        output=out,
    )

    assert "https://example.com/bad-redirect" not in scraped_urls, (
        "URL with malformed Location header must be skipped"
    )
    assert "https://example.com/safe" in scraped_urls, "safe URL must still be scraped"
