"""Tests for crawler.sitemap — three-tier URL discovery."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.crawler.sitemap import discover_urls

_FIXTURES = Path(__file__).parent / "fixtures" / "sitemaps"


def _sitemap_xml() -> str:
    return (_FIXTURES / "example_sitemap.xml").read_text()


def _robots_txt() -> str:
    return (_FIXTURES / "example_robots.txt").read_text()


# ---------------------------------------------------------------------------
# Tier 1 — /sitemap.xml exists and parses
# ---------------------------------------------------------------------------


async def test_discovers_from_sitemap_xml(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=200,
        text=_sitemap_xml(),
        headers={"content-type": "application/xml"},
    )

    urls = await discover_urls("https://example.com/", max_urls=100)

    assert "https://example.com/" in urls
    assert "https://example.com/about" in urls
    assert "https://example.com/blog/post-1" in urls
    assert "https://example.com/blog/post-2" in urls
    assert len(urls) == 4


# ---------------------------------------------------------------------------
# Tier 2 — /sitemap.xml missing → /robots.txt referenced
# ---------------------------------------------------------------------------


async def test_falls_back_to_robots_txt_sitemap_directive(httpx_mock: HTTPXMock) -> None:
    # Tier 1 — /sitemap.xml absent.
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    # Tier 2 — robots.txt references a *different* sitemap URL (realistic: many
    # sites use /sitemap_index.xml or /sitemap-pages.xml in robots.txt).
    # Using a distinct URL also ensures the visited-set doesn't suppress it.
    httpx_mock.add_response(
        url="https://example.com/robots.txt",
        status_code=200,
        text="User-agent: *\nDisallow:\nSitemap: https://example.com/sitemap-pages.xml",
        headers={"content-type": "text/plain"},
    )
    httpx_mock.add_response(
        url="https://example.com/sitemap-pages.xml",
        status_code=200,
        text=_sitemap_xml(),
        headers={"content-type": "application/xml"},
    )

    urls = await discover_urls("https://example.com/", max_urls=100)

    assert len(urls) == 4


# ---------------------------------------------------------------------------
# Tier 3 — sitemap missing, robots empty → BFS from root
# ---------------------------------------------------------------------------


async def test_bfs_fallback_when_no_sitemap(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        html=(
            "<html><body>"
            '<a href="/about">About</a>'
            '<a href="/blog/post-1">Post 1</a>'
            '<a href="https://other.com/external">External</a>'
            '<a href="mailto:foo@example.com">Email</a>'
            "</body></html>"
        ),
        headers={"content-type": "text/html"},
    )

    urls = await discover_urls("https://example.com/", max_urls=100)

    assert "https://example.com/about" in urls
    assert "https://example.com/blog/post-1" in urls
    # External and non-http filtered out
    assert "https://other.com/external" not in urls
    assert "mailto:foo@example.com" not in urls


# ---------------------------------------------------------------------------
# max_urls cap honored
# ---------------------------------------------------------------------------


async def test_max_urls_cap(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=200,
        text=_sitemap_xml(),
        headers={"content-type": "application/xml"},
    )

    urls = await discover_urls("https://example.com/", max_urls=2)

    assert len(urls) == 2


# ---------------------------------------------------------------------------
# Sitemap-index (nested) is supported
# ---------------------------------------------------------------------------


async def test_sitemap_index_recursion(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=200,
        text=(
            '<?xml version="1.0"?>'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<sitemap><loc>https://example.com/sub.xml</loc></sitemap>"
            "</sitemapindex>"
        ),
        headers={"content-type": "application/xml"},
    )
    httpx_mock.add_response(
        url="https://example.com/sub.xml",
        status_code=200,
        text=_sitemap_xml(),
        headers={"content-type": "application/xml"},
    )

    urls = await discover_urls("https://example.com/", max_urls=100)

    assert len(urls) == 4


# ---------------------------------------------------------------------------
# Empty result when nothing discoverable
# ---------------------------------------------------------------------------


async def test_returns_empty_when_nothing_found(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.com/", status_code=404)

    urls = await discover_urls("https://example.com/", max_urls=100)

    assert urls == []


# ---------------------------------------------------------------------------
# Recursion guard — self-referential sitemap index
# ---------------------------------------------------------------------------


async def test_walk_sitemap_breaks_index_self_reference(httpx_mock: HTTPXMock) -> None:
    """Sitemap index that references itself MUST NOT cause RecursionError."""
    self_ref_xml = (
        '<?xml version="1.0"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://example.com/sitemap.xml</loc></sitemap>"
        "</sitemapindex>"
    )
    # The mock is hit once (the second call is suppressed by the visited-set).
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=200,
        text=self_ref_xml,
        headers={"content-type": "application/xml"},
    )
    # Tier 2 and 3 fallbacks should not be reached.
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    httpx_mock.add_response(url="https://example.com/", status_code=404)

    # Should return [] (no page URLs in a pure sitemapindex) without raising.
    urls = await discover_urls("https://example.com/", max_urls=100)
    assert urls == []


# ---------------------------------------------------------------------------
# Budget short-circuit — large sitemap index stops early
# ---------------------------------------------------------------------------


async def test_walk_sitemap_caps_index_traversal_at_max_urls(httpx_mock: HTTPXMock) -> None:
    """Once max_urls is reached, no further nested sitemaps are fetched."""
    # Top-level sitemapindex references 3 nested sitemaps.
    index_xml = (
        '<?xml version="1.0"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://example.com/s1.xml</loc></sitemap>"
        "<sitemap><loc>https://example.com/s2.xml</loc></sitemap>"
        "<sitemap><loc>https://example.com/s3.xml</loc></sitemap>"
        "</sitemapindex>"
    )

    def _urlset(n: int) -> str:
        """Return a urlset with *n* page URLs."""
        locs = "".join(
            f"<url><loc>https://example.com/page-{n}-{i}</loc></url>"
            for i in range(n)
        )
        return (
            '<?xml version="1.0"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{locs}"
            "</urlset>"
        )

    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=200,
        text=index_xml,
        headers={"content-type": "application/xml"},
    )
    httpx_mock.add_response(
        url="https://example.com/s1.xml",
        status_code=200,
        text=_urlset(10),
        headers={"content-type": "application/xml"},
    )
    # s2 and s3 should NOT be fetched when max_urls=5 is satisfied by s1.

    urls = await discover_urls("https://example.com/", max_urls=5)

    # Exactly 5 URLs returned (capped).
    assert len(urls) == 5
    # All from s1 (page-10-*).
    assert all("page-10-" in u for u in urls)
