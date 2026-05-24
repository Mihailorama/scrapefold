"""Tests for crawler.filters — pure URL classification + filtering."""

from __future__ import annotations

import pytest

from scrapefold.crawler.filters import (
    filter_urls,
    is_crawlable,
    strip_tracking_params,
)

# ---------------------------------------------------------------------------
# 1. is_crawlable — accepts HTTP(S) URLs to plausible HTML resources
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/blog/post-1",
        "http://example.com/path?q=1",
    ],
)
def test_is_crawlable_accepts_html_urls(url: str) -> None:
    assert is_crawlable(url) is True


# ---------------------------------------------------------------------------
# 2. is_crawlable — rejects non-HTTP schemes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "mailto:foo@example.com",
        "tel:+1234567890",
        "javascript:void(0)",
        "ftp://example.com/file",
        "#fragment-only",
        "",
    ],
)
def test_is_crawlable_rejects_non_http_schemes(url: str) -> None:
    assert is_crawlable(url) is False


# ---------------------------------------------------------------------------
# 3. is_crawlable — rejects non-HTML file extensions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/file.pdf",
        "https://example.com/image.jpg",
        "https://example.com/photo.PNG",
        "https://example.com/archive.zip",
        "https://example.com/video.mp4",
        "https://example.com/style.css",
        "https://example.com/app.js",
    ],
)
def test_is_crawlable_rejects_binary_extensions(url: str) -> None:
    assert is_crawlable(url) is False


# ---------------------------------------------------------------------------
# 4. is_crawlable — rejects login / share / utm-only paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/login",
        "https://example.com/signin",
        "https://example.com/auth/callback",
        "https://example.com/share?via=twitter",
    ],
)
def test_is_crawlable_rejects_auth_share_paths(url: str) -> None:
    assert is_crawlable(url) is False


# ---------------------------------------------------------------------------
# 5. strip_tracking_params — removes utm_*, fbclid, gclid
# ---------------------------------------------------------------------------


def test_strip_tracking_params_removes_utm() -> None:
    url = "https://example.com/post?utm_source=fb&utm_medium=social&id=42"
    assert strip_tracking_params(url) == "https://example.com/post?id=42"


def test_strip_tracking_params_removes_fbclid_gclid() -> None:
    url = "https://example.com/p?fbclid=ABC&gclid=XYZ&page=2"
    assert strip_tracking_params(url) == "https://example.com/p?page=2"


def test_strip_tracking_params_empty_query_drops_question_mark() -> None:
    url = "https://example.com/p?utm_source=x"
    assert strip_tracking_params(url) == "https://example.com/p"


def test_strip_tracking_params_no_query_unchanged() -> None:
    url = "https://example.com/about"
    assert strip_tracking_params(url) == url


# ---------------------------------------------------------------------------
# 6. filter_urls — same-host, dedup, strip tracking, drop non-crawlable
# ---------------------------------------------------------------------------


def test_filter_urls_keeps_same_host_only() -> None:
    urls = [
        "https://example.com/a",
        "https://other.com/b",
        "https://example.com/c",
    ]
    result = filter_urls(urls, root="https://example.com/")
    assert result == ["https://example.com/a", "https://example.com/c"]


def test_filter_urls_dedups_after_tracking_strip() -> None:
    urls = [
        "https://example.com/p?utm_source=fb",
        "https://example.com/p?utm_source=tw",
        "https://example.com/p",
    ]
    result = filter_urls(urls, root="https://example.com/")
    assert result == ["https://example.com/p"]


def test_filter_urls_drops_non_crawlable() -> None:
    urls = [
        "https://example.com/a",
        "https://example.com/file.pdf",
        "mailto:foo@example.com",
        "https://example.com/b",
    ]
    result = filter_urls(urls, root="https://example.com/")
    assert result == ["https://example.com/a", "https://example.com/b"]


def test_filter_urls_treats_www_and_apex_as_same_host() -> None:
    urls = [
        "https://www.example.com/a",
        "https://example.com/b",
    ]
    result = filter_urls(urls, root="https://example.com/")
    assert set(result) == {"https://www.example.com/a", "https://example.com/b"}


# ---------------------------------------------------------------------------
# 7. filter_urls — fragment stripping + dedup
# ---------------------------------------------------------------------------


def test_filter_urls_dedupes_after_fragment_strip() -> None:
    """URLs differing only in fragment must dedupe to one entry."""
    urls = [
        "https://example.com/page",
        "https://example.com/page#intro",
        "https://example.com/page#pricing",
    ]
    result = filter_urls(urls, root="https://example.com/")
    assert result == ["https://example.com/page"]


# ---------------------------------------------------------------------------
# 8. filter_urls — follow_subdomains gate
# ---------------------------------------------------------------------------


def test_filter_urls_default_excludes_arbitrary_subdomains() -> None:
    """follow_subdomains=False (default) rejects admin.example.com when root is example.com."""
    urls = [
        "https://example.com/a",
        "https://admin.example.com/b",
        "https://cdn.example.com/c",
        "https://www.example.com/d",  # www is the conventional apex-equivalent
    ]
    result = filter_urls(urls, root="https://example.com/")
    assert "https://admin.example.com/b" not in result
    assert "https://cdn.example.com/c" not in result
    assert "https://example.com/a" in result
    assert "https://www.example.com/d" in result


def test_filter_urls_follow_subdomains_true_accepts_subdomains() -> None:
    """follow_subdomains=True keeps admin.example.com when registered domain matches."""
    urls = [
        "https://example.com/a",
        "https://admin.example.com/b",
    ]
    result = filter_urls(urls, root="https://example.com/", follow_subdomains=True)
    assert "https://admin.example.com/b" in result


# ---------------------------------------------------------------------------
# 9. Malformed URL robustness (Codex round 8 P2)
# ---------------------------------------------------------------------------


def test_is_crawlable_rejects_malformed_ipv6_url() -> None:
    """Malformed URLs must be skipped, not raise ValueError."""
    assert is_crawlable("http://[::1") is False
    assert is_crawlable("http://[invalid") is False


def test_strip_tracking_params_passthrough_on_parse_failure() -> None:
    """Malformed URLs round-trip unchanged through strip_tracking_params."""
    assert strip_tracking_params("http://[::1") == "http://[::1"


def test_filter_urls_skips_malformed_entries() -> None:
    """A malformed URL inside a sitemap-derived list must not crash filter_urls."""
    urls = [
        "https://example.com/good",
        "http://[::1",  # malformed — must be dropped silently
        "https://example.com/also-good",
    ]
    result = filter_urls(urls, root="https://example.com")
    assert result == ["https://example.com/good", "https://example.com/also-good"]
