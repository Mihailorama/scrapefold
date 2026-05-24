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
    # BFS now fetches linked pages too — provide minimal responses.
    httpx_mock.add_response(
        url="https://example.com/about",
        status_code=200,
        html="<html><body>About page</body></html>",
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url="https://example.com/blog/post-1",
        status_code=200,
        html="<html><body>Post 1</body></html>",
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
        locs = "".join(f"<url><loc>https://example.com/page-{n}-{i}</loc></url>" for i in range(n))
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


# ---------------------------------------------------------------------------
# Finding 1 (Codex P1): SSRF guard — off-host sitemap URLs must not be fetched
# ---------------------------------------------------------------------------


async def test_walk_sitemap_rejects_offhost_loc_in_index(httpx_mock: HTTPXMock) -> None:
    """A sitemap-index whose <loc> points off-host MUST NOT cause an off-host fetch."""
    off_host_url = "http://internal.corp/secret.xml"
    index_xml = (
        '<?xml version="1.0"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<sitemap><loc>{off_host_url}</loc></sitemap>"
        "</sitemapindex>"
    )
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=200,
        text=index_xml,
        headers={"content-type": "application/xml"},
    )
    # Tier 2: robots.txt has no extra sitemaps
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    # Tier 3 BFS fallback: BFS root fetch
    httpx_mock.add_response(url="https://example.com/", status_code=404)

    urls = await discover_urls("https://example.com/", max_urls=100)

    # No request to internal.corp should have been made.
    all_requests = httpx_mock.get_requests()
    assert not any("internal.corp" in str(r.url) for r in all_requests), (
        f"Off-host URL was fetched: {[r.url for r in all_requests]}"
    )
    assert urls == []


async def test_discover_urls_rejects_offhost_robots_sitemap_directive(
    httpx_mock: HTTPXMock,
) -> None:
    """A robots.txt Sitemap: directive pointing off-host MUST NOT be fetched."""
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(
        url="https://example.com/robots.txt",
        status_code=200,
        text="User-agent: *\nDisallow:\nSitemap: http://internal.corp/x.xml\n",
        headers={"content-type": "text/plain"},
    )
    # Tier 3 BFS root — also 404 so nothing leaks
    httpx_mock.add_response(url="https://example.com/", status_code=404)

    urls = await discover_urls("https://example.com/", max_urls=100)

    all_requests = httpx_mock.get_requests()
    assert not any("internal.corp" in str(r.url) for r in all_requests), (
        f"Off-host directive was fetched: {[r.url for r in all_requests]}"
    )
    assert urls == []


# ---------------------------------------------------------------------------
# Finding 2 (Codex P2): True BFS up to max_depth
# ---------------------------------------------------------------------------


async def test_bfs_fallback_walks_to_max_depth(httpx_mock: HTTPXMock) -> None:
    """When no sitemap exists, BFS walks the link graph up to max_depth."""
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    # depth 0 — root
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        html='<html><body><a href="/docs">Docs</a></body></html>',
        headers={"content-type": "text/html"},
    )
    # depth 1
    httpx_mock.add_response(
        url="https://example.com/docs",
        status_code=200,
        html='<html><body><a href="/docs/getting-started">Getting Started</a></body></html>',
        headers={"content-type": "text/html"},
    )
    # depth 2
    httpx_mock.add_response(
        url="https://example.com/docs/getting-started",
        status_code=200,
        html="<html><body>Getting started content</body></html>",
        headers={"content-type": "text/html"},
    )

    urls = await discover_urls("https://example.com/", max_urls=100, max_depth=3)

    assert "https://example.com/" in urls
    assert "https://example.com/docs" in urls
    assert "https://example.com/docs/getting-started" in urls


async def test_bfs_fallback_respects_max_urls_cap(httpx_mock: HTTPXMock) -> None:
    """BFS stops once max_urls is reached, even mid-level."""
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    # root links to 5 pages; only the first 2 need to be fetched for max_urls=3
    # (root itself counts as 1, then page-0 and page-1 bring it to 3).
    links_html = "".join(f'<a href="/page-{i}">P{i}</a>' for i in range(5))
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        html=f"<html><body>{links_html}</body></html>",
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url="https://example.com/page-0",
        status_code=200,
        html="<html><body>page 0</body></html>",
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url="https://example.com/page-1",
        status_code=200,
        html="<html><body>page 1</body></html>",
        headers={"content-type": "text/html"},
    )

    urls = await discover_urls("https://example.com/", max_urls=3, max_depth=3)

    assert len(urls) <= 3


async def test_bfs_fallback_off_host_links_excluded(httpx_mock: HTTPXMock) -> None:
    """BFS does not queue off-host links."""
    httpx_mock.add_response(url="https://example.com/sitemap.xml", status_code=404)
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    httpx_mock.add_response(
        url="https://example.com/",
        status_code=200,
        html=(
            '<html><body><a href="/a">A</a><a href="https://other.com/x">External</a></body></html>'
        ),
        headers={"content-type": "text/html"},
    )
    httpx_mock.add_response(
        url="https://example.com/a",
        status_code=200,
        html="<html><body>Page A</body></html>",
        headers={"content-type": "text/html"},
    )

    urls = await discover_urls("https://example.com/", max_urls=100, max_depth=3)

    assert "https://example.com/a" in urls
    assert "https://other.com/x" not in urls

    all_requests = httpx_mock.get_requests()
    assert not any("other.com" in str(r.url) for r in all_requests), (
        f"Off-host URL was fetched: {[r.url for r in all_requests]}"
    )


# ---------------------------------------------------------------------------
# Finding 1 (Codex P1 round-3): SSRF via HTTP redirect
# ---------------------------------------------------------------------------


@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_sitemap_fetch_rejects_offhost_redirect(httpx_mock: HTTPXMock) -> None:
    """A /sitemap.xml that 302s to an off-host URL MUST NOT be followed."""
    # /sitemap.xml returns 302 → internal.corp
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        status_code=302,
        headers={"location": "http://internal.corp/x.xml"},
        text="",
    )
    # Tier 2: robots.txt absent
    httpx_mock.add_response(url="https://example.com/robots.txt", status_code=404)
    # Tier 3 BFS may attempt root — provide 404s so it yields nothing
    httpx_mock.add_response(url="https://example.com", status_code=404)
    httpx_mock.add_response(url="https://example.com/", status_code=404)

    urls = await discover_urls("https://example.com", max_urls=100)

    all_requests = httpx_mock.get_requests()
    assert not any("internal.corp" in str(r.url) for r in all_requests), (
        f"Off-host redirect target was fetched: {[r.url for r in all_requests]}"
    )
    assert urls == []
