"""Tests for crawler.sitemap — three-tier URL discovery."""

from __future__ import annotations

from pathlib import Path

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
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(
        url="https://example.com/robots.txt",
        status_code=200,
        text=_robots_txt(),
        headers={"content-type": "text/plain"},
    )
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
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
