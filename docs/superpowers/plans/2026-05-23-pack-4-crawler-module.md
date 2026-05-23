# Pack 4 — Crawler module + router tech-debt P1 #3 + P1 #7

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PACK-OPEN REFRESH:** This plan was written 2026-05-23 alongside Packs 3 and 5-8. Before starting, re-run `scripts/check-deps-fresh.sh` (lands in Pack 3) and verify the dep floors are current. If `httpx`, `tldextract`, or `beautifulsoup4` had a major bump since the plan was written, adjust the code blocks below accordingly.

**Goal:** Land the `crawler/` package (sitemap → BFS → filters → stitcher), wire `crawl_site()` to use it, plus router tech-debt P1 #3 (per-engine `avg_response_mb` wired through `_estimate_step_cost`) and P1 #7 (probe cache).

**Architecture:** Four sub-modules under `src/scrapefold/crawler/`. `sitemap.py` discovers URLs (sitemap.xml → robots.txt → BFS fallback). `filters.py` drops mailto/tel/utm/login/non-HTML. `stitcher.py` writes one .md with YAML front-matter per page. `crawler/__init__.py` is the orchestrator that wires these into `crawl_site()`. The router gains a module-level probe cache and per-engine MB estimates.

**Tech Stack:** Python 3.10+, httpx, tldextract (already pinned), BeautifulSoup4 (already pinned for HTML parsing), pyyaml (already pinned for front-matter), pytest, pytest-httpx.

**Spec reference:** `docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md` §3.2.

---

## Phase A — Dep-freshness audit + pack-opening CHANGELOG

### Task A.1 — Dep-freshness audit

- [ ] **Step 1: Run audit**

Run: `./scripts/check-deps-fresh.sh > /tmp/pack-4-deps.txt && cat /tmp/pack-4-deps.txt`

- [ ] **Step 2: Bump stale floors**

For any dep ≥ 2 minor versions behind, bump the lower-bound pin in `pyproject.toml`. Reinstall (`pip install -e ".[test]" --upgrade`), run `./scripts/check.sh`. If a major bump breaks a test, fix the affected engine adapter inside this pack (do not defer).

- [ ] **Step 3: Commit if anything changed**

```bash
git add pyproject.toml
git commit -m "chore: refresh dependency floors for Pack 4"
```

If nothing changed, skip the commit.

---

## Phase B — `crawler/filters.py` (URL filters; pure functions, easiest first)

### Task B.1 — Failing tests for filters

**Files:**
- Create: `tests/test_crawler_filters.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_crawler_filters.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_crawler_filters.py -v`
Expected: every test fails with `ModuleNotFoundError: scrapefold.crawler.filters`.

### Task B.2 — Implement `crawler/filters.py`

**Files:**
- Create: `src/scrapefold/crawler/__init__.py` (empty for now)
- Create: `src/scrapefold/crawler/filters.py`

- [ ] **Step 1: Create empty `crawler/__init__.py`**

Create `src/scrapefold/crawler/__init__.py`:

```python
"""crawler — sitemap discovery + filtering + stitching."""
```

- [ ] **Step 2: Create `crawler/filters.py`**

Create `src/scrapefold/crawler/filters.py`:

```python
"""URL filtering — pure functions used by the crawler before scraping.

Three responsibilities:
- ``is_crawlable(url)``: scheme / extension / well-known-path gate.
- ``strip_tracking_params(url)``: drop utm_*, fbclid, gclid query params.
- ``filter_urls(urls, root)``: same-host dedup + tracking strip + crawlable.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import tldextract

_NON_HTML_EXTS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".zip",
        ".tar",
        ".gz",
        ".mp4",
        ".mp3",
        ".wav",
        ".css",
        ".js",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".xml",  # sitemap.xml is fetched separately, not crawled as content
        ".rss",
    }
)

_AUTH_SHARE_SEGMENTS: frozenset[str] = frozenset(
    {"login", "signin", "signup", "register", "auth", "logout", "share"}
)

_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)
_TRACKING_PARAM_EXACT: frozenset[str] = frozenset(
    {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga", "yclid"}
)


def is_crawlable(url: str) -> bool:
    """Return True if *url* points at something the crawler should fetch.

    Rejects: non-http(s) schemes, empty/fragment-only, common binary
    extensions, and well-known auth/share path segments.
    """
    if not url or url.startswith("#"):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in _NON_HTML_EXTS):
        return False
    segments = [s for s in path_lower.split("/") if s]
    if segments and segments[0] in _AUTH_SHARE_SEGMENTS:
        return False
    return True


def strip_tracking_params(url: str) -> str:
    """Drop utm_*, fbclid, gclid, msclkid, etc. from the URL's query string."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.startswith(_TRACKING_PARAM_PREFIXES) and k not in _TRACKING_PARAM_EXACT
    ]
    new_query = urlencode(kept)
    return urlunparse(parsed._replace(query=new_query))


def _registered_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def filter_urls(urls: list[str], *, root: str) -> list[str]:
    """Filter a URL list to same-host crawlable URLs with tracking stripped.

    Order-preserving; deduplicated; empties returned as an empty list.
    """
    root_domain = _registered_domain(root)
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        if not is_crawlable(raw):
            continue
        if _registered_domain(raw) != root_domain:
            continue
        cleaned = strip_tracking_params(raw)
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


__all__ = ["filter_urls", "is_crawlable", "strip_tracking_params"]
```

- [ ] **Step 3: Run filters tests**

Run: `pytest tests/test_crawler_filters.py -v`
Expected: all 16 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/scrapefold/crawler/__init__.py src/scrapefold/crawler/filters.py tests/test_crawler_filters.py
git commit -m "$(cat <<'EOF'
feat: Pack 4 — crawler/filters.py (URL filtering)

Three pure functions: is_crawlable (scheme / extension / well-known-path
gate), strip_tracking_params (utm_*, fbclid, gclid drop), filter_urls
(same-host + dedup + tracking strip via the registered domain from
tldextract).

16 tests covering accept/reject lists + parametrized binary-extension
and auth-share-path matrices.

Spec: docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md §3.2
EOF
)"
```

---

## Phase C — `crawler/sitemap.py`

Three-tier discovery: `/sitemap.xml` → `/robots.txt` `Sitemap:` directive → same-host BFS via the root page's `<a href>` links.

### Task C.1 — Failing tests

**Files:**
- Create: `tests/test_crawler_sitemap.py`
- Create: `tests/fixtures/__init__.py` (empty)
- Create: `tests/fixtures/sitemaps/example_sitemap.xml`
- Create: `tests/fixtures/sitemaps/example_robots.txt`

- [ ] **Step 1: Create fixture files**

`tests/fixtures/__init__.py`: empty file.

`tests/fixtures/sitemaps/example_sitemap.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/</loc></url>
  <url><loc>https://example.com/about</loc></url>
  <url><loc>https://example.com/blog/post-1</loc></url>
  <url><loc>https://example.com/blog/post-2</loc></url>
</urlset>
```

`tests/fixtures/sitemaps/example_robots.txt`:

```
User-agent: *
Disallow: /admin

Sitemap: https://example.com/sitemap.xml
```

- [ ] **Step 2: Create the test file**

Create `tests/test_crawler_sitemap.py`:

```python
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
            '<html><body>'
            '<a href="/about">About</a>'
            '<a href="/blog/post-1">Post 1</a>'
            '<a href="https://other.com/external">External</a>'
            '<a href="mailto:foo@example.com">Email</a>'
            '</body></html>'
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
            '<sitemap><loc>https://example.com/sub.xml</loc></sitemap>'
            '</sitemapindex>'
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

    assert urls == ["https://example.com/"]  # root is always included if reachable, OR…
    # Acceptable alternative: empty list. Pick one in implementation and pin it.
```

- [ ] **Step 3: Resolve the ambiguity in the last test before implementing**

Pick one of the two contracts:
- (a) `discover_urls` always includes the root URL in its output, even if it fails to fetch.
- (b) `discover_urls` returns `[]` when nothing is reachable.

Implementation choice: **(b)** — empty list signals "nothing discoverable", caller decides. Edit `test_returns_empty_when_nothing_found` to assert `urls == []`.

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_crawler_sitemap.py -v`
Expected: `ModuleNotFoundError: scrapefold.crawler.sitemap`.

### Task C.2 — Implement `crawler/sitemap.py`

**Files:**
- Create: `src/scrapefold/crawler/sitemap.py`

- [ ] **Step 1: Create the module**

Create `src/scrapefold/crawler/sitemap.py`:

```python
"""URL discovery — sitemap.xml → robots.txt → BFS fallback.

Public API: ``async def discover_urls(root, *, max_urls) -> list[str]``.
Returns filtered (same-host, crawlable, dedup) URLs in discovery order,
capped at *max_urls*.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from scrapefold.crawler.filters import filter_urls

logger = logging.getLogger(__name__)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*Sitemap:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.debug("sitemap: GET %s failed: %s", url, exc)
        return None
    return resp


def _parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls) from a sitemap XML body."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("sitemap: XML parse failed: %s", exc)
        return [], []

    if root.tag.endswith("sitemapindex"):
        nested = [el.text.strip() for el in root.iter(f"{_SITEMAP_NS}loc") if el.text]
        return [], nested

    if root.tag.endswith("urlset"):
        pages = [el.text.strip() for el in root.iter(f"{_SITEMAP_NS}loc") if el.text]
        return pages, []

    return [], []


def _parse_robots_for_sitemaps(text: str) -> list[str]:
    return [m.group(1).strip() for m in _ROBOTS_SITEMAP_RE.finditer(text)]


def _bfs_links_from_html(html: str, *, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        out.append(urljoin(base_url, href))
    return out


async def discover_urls(root: str, *, max_urls: int = 100) -> list[str]:
    """Discover same-host crawlable URLs from *root*, capped at *max_urls*.

    Tries `<root>/sitemap.xml`, then `<root>/robots.txt` Sitemap directives,
    then BFS from the root page's links. Returns filtered, deduped,
    discovery-order URLs.
    """
    if max_urls <= 0:
        return []

    parsed = urlparse(root)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = f"{origin}/sitemap.xml"
    robots_url = f"{origin}/robots.txt"

    found: list[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Tier 1 — /sitemap.xml direct
        await _walk_sitemap(client, sitemap_url, found)

        # Tier 2 — /robots.txt sitemap directives
        if not found:
            robots_resp = await _fetch(client, robots_url)
            if robots_resp is not None and robots_resp.status_code == 200:
                for nested in _parse_robots_for_sitemaps(robots_resp.text):
                    await _walk_sitemap(client, nested, found)

        # Tier 3 — BFS from root page
        if not found:
            root_resp = await _fetch(client, root)
            if root_resp is not None and root_resp.status_code == 200:
                ct = root_resp.headers.get("content-type", "").lower()
                if "html" in ct or "<html" in root_resp.text[:512].lower():
                    found = _bfs_links_from_html(root_resp.text, base_url=root)

    filtered = filter_urls(found, root=root)
    return filtered[:max_urls]


async def _walk_sitemap(
    client: httpx.AsyncClient, sitemap_url: str, found: list[str]
) -> None:
    """Fetch *sitemap_url* and recurse into nested sitemap-index entries."""
    resp = await _fetch(client, sitemap_url)
    if resp is None or resp.status_code != 200:
        return
    pages, nested = _parse_sitemap(resp.text)
    found.extend(pages)
    for n in nested:
        await _walk_sitemap(client, n, found)


__all__ = ["discover_urls"]
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_crawler_sitemap.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/scrapefold/crawler/sitemap.py tests/test_crawler_sitemap.py tests/fixtures/__init__.py tests/fixtures/sitemaps/example_sitemap.xml tests/fixtures/sitemaps/example_robots.txt
git commit -m "$(cat <<'EOF'
feat: Pack 4 — crawler/sitemap.py (three-tier URL discovery)

discover_urls(root, max_urls) tries /sitemap.xml → /robots.txt Sitemap
directives → BFS from root page <a href> links. Supports sitemap-index
recursion, content-type-aware HTML sniffing, same-host filtering via
crawler.filters.

6 tests + fixture sitemap.xml + robots.txt.
EOF
)"
```

---

## Phase D — `crawler/stitcher.py`

Write a single `.md` file with one section per page: a YAML front-matter block (`url`, `engine`, `elapsed_ms`, `fetched_at`) followed by the page's markdown.

### Task D.1 — Failing tests

**Files:**
- Create: `tests/test_crawler_stitcher.py`

- [ ] **Step 1: Write tests**

Create `tests/test_crawler_stitcher.py`:

```python
"""Tests for crawler.stitcher — multi-page markdown writer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scrapefold.crawler.stitcher import write_stitched
from scrapefold.result import ScrapeResult


def _result(url: str, md: str, engine: str = "requests") -> ScrapeResult:
    return ScrapeResult(
        url=url, text=md, markdown=md, html=None, engine=engine, elapsed_ms=42
    )


def test_writes_single_file_with_two_sections(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    results = [
        _result("https://example.com/a", "# A\n\nAlpha body."),
        _result("https://example.com/b", "# B\n\nBeta body."),
    ]
    write_stitched(results, out)

    text = out.read_text()
    # Two front-matter blocks
    assert text.count("---\n") >= 4  # 2 open + 2 close
    assert "# A" in text
    assert "# B" in text


def test_front_matter_is_valid_yaml(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    write_stitched(
        [_result("https://example.com/a", "# A\n\nAlpha.")],
        out,
    )
    text = out.read_text()

    # Pull the first front-matter block
    start = text.index("---\n") + len("---\n")
    end = text.index("\n---\n", start)
    meta = yaml.safe_load(text[start:end])

    assert meta["url"] == "https://example.com/a"
    assert meta["engine"] == "requests"
    assert meta["elapsed_ms"] == 42
    assert isinstance(meta["fetched_at"], str)
    # ISO-8601 round-trip
    datetime.fromisoformat(meta["fetched_at"])


def test_empty_results_writes_empty_file(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    write_stitched([], out)
    assert out.read_text() == ""


def test_creates_parent_directories(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "deep" / "site.md"
    write_stitched(
        [_result("https://example.com/", "# Root")],
        out,
    )
    assert out.exists()


def test_returns_path(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    returned = write_stitched(
        [_result("https://example.com/", "# Root")],
        out,
    )
    assert returned == out
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_crawler_stitcher.py -v`
Expected: `ModuleNotFoundError`.

### Task D.2 — Implement `crawler/stitcher.py`

**Files:**
- Create: `src/scrapefold/crawler/stitcher.py`

- [ ] **Step 1: Create the module**

Create `src/scrapefold/crawler/stitcher.py`:

```python
"""Stitcher — write a list of ScrapeResults into a single multi-section .md file."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from scrapefold.result import ScrapeResult


def _front_matter(result: ScrapeResult) -> str:
    meta = {
        "url": result.url,
        "engine": result.engine,
        "elapsed_ms": result.elapsed_ms,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return "---\n" + yaml.safe_dump(meta, sort_keys=True) + "---\n"


def write_stitched(results: list[ScrapeResult], output: Path) -> Path:
    """Write *results* to *output* as one markdown file with per-page front-matter.

    Empty results produce an empty file. Parent directories are created as
    needed. Returns the output path.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not results:
        output.write_text("")
        return output

    sections: list[str] = []
    for r in results:
        sections.append(_front_matter(r) + "\n" + r.markdown.rstrip() + "\n")

    output.write_text("\n".join(sections))
    return output


__all__ = ["write_stitched"]
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_crawler_stitcher.py -v`
Expected: all 5 PASS.

- [ ] **Step 3: Commit**

```bash
git add src/scrapefold/crawler/stitcher.py tests/test_crawler_stitcher.py
git commit -m "$(cat <<'EOF'
feat: Pack 4 — crawler/stitcher.py (multi-page markdown writer)

write_stitched(results, output) writes one .md file with a YAML
front-matter block per page (url, engine, elapsed_ms, fetched_at) and
the page's markdown between section separators. Empty results yield
an empty file; parent dirs auto-created.

5 tests including YAML round-trip validation.
EOF
)"
```

---

## Phase E — `crawl_site()` public API

Wire `sitemap.discover_urls` → `filter_urls` → `scrape()` per URL → `stitcher.write_stitched`.

### Task E.1 — Failing tests

**Files:**
- Modify: `tests/test_smoke.py` (replace the `NotImplementedError` test for `crawl_site`)

- [ ] **Step 1: Replace the placeholder test in `tests/test_smoke.py`**

In `tests/test_smoke.py`, remove the existing test:

```python
async def test_public_crawl_site_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await scrapefold.crawl_site("https://example.com")
```

Add a new test file `tests/test_crawl_site.py` (full coverage; smoke stays smoke):

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_crawl_site.py -v`
Expected: ImportError or AttributeError because `scrapefold.crawl_site` still raises `NotImplementedError`.

### Task E.2 — Implement `crawl_site()`

**Files:**
- Modify: `src/scrapefold/__init__.py`
- Modify: `src/scrapefold/crawler/__init__.py`

- [ ] **Step 1: Implement the orchestrator in `crawler/__init__.py`**

Replace `src/scrapefold/crawler/__init__.py` content with:

```python
"""crawler — sitemap discovery + filtering + stitching.

Public entry point: ``async def crawl(root, opts, output) -> Path``.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from scrapefold.crawler.sitemap import discover_urls
from scrapefold.crawler.stitcher import write_stitched
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)


async def crawl(
    root: str,
    opts: ScrapeOptions | None = None,
    output: Path | str | None = None,
) -> Path:
    """Walk a site → produce one stitched markdown file.

    Discovery: sitemap → robots → BFS (see ``crawler.sitemap``).
    Per-URL fetch delegates to ``scrapefold.scrape``.
    Output: ``crawler.stitcher.write_stitched``.
    """
    from scrapefold import scrape  # local import — avoids circular

    opts = opts or ScrapeOptions()
    max_pages = opts.max_pages if opts.max_pages else 100

    urls = await discover_urls(root, max_urls=max_pages)
    logger.info("crawler: discovered %d urls from %s", len(urls), root)

    if not urls:
        urls = [root]  # at least try the root

    results: list[ScrapeResult] = []
    for url in urls:
        try:
            results.append(await scrape(url, opts))
        except Exception as exc:  # noqa: BLE001 — caller-side failure logging
            logger.warning("crawler: scrape failed url=%s err=%s", url, exc)

    if output is None:
        output = Path(tempfile.gettempdir()) / "scrapefold-crawl.md"
    output = Path(output)

    return write_stitched(results, output)


__all__ = ["crawl"]
```

- [ ] **Step 2: Replace `crawl_site` stub in `src/scrapefold/__init__.py`**

In `src/scrapefold/__init__.py`, replace:

```python
async def crawl_site(url: str, opts: ScrapeOptions | None = None, **kwargs: object) -> str:
    """Whole-site crawl → single markdown file.

    Scaffold stub — crawler module lands in S8.
    """
    raise NotImplementedError("crawl_site() lands in S8 (crawler module).")
```

with:

```python
async def crawl_site(
    url: str,
    opts: ScrapeOptions | None = None,
    output: object | None = None,
) -> object:
    """Whole-site crawl → single markdown file.

    Discovers URLs from ``url`` (sitemap → robots → BFS), scrapes each
    via ``scrape()``, and writes a stitched .md file at ``output``
    (defaults to ``<tmp>/scrapefold-crawl.md``). Returns the output Path.
    """
    from scrapefold.crawler import crawl

    return await crawl(url, opts=opts, output=output)  # type: ignore[arg-type]
```

- [ ] **Step 3: Drop the placeholder test from `test_smoke.py`**

Remove:

```python
async def test_public_crawl_site_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await scrapefold.crawl_site("https://example.com")
```

- [ ] **Step 4: Run all crawl_site tests**

Run: `pytest tests/test_crawl_site.py tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `./scripts/check.sh`
Expected: `=== All checks passed ===`.

- [ ] **Step 6: Commit**

```bash
git add src/scrapefold/__init__.py src/scrapefold/crawler/__init__.py tests/test_crawl_site.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat: Pack 4 — crawl_site() public API wired to crawler/

scrapefold.crawl_site(url, opts, output) delegates to crawler.crawl
which orchestrates: sitemap.discover_urls → scrape() per URL →
stitcher.write_stitched. Per-URL failures are logged but do not abort
the crawl. Default output is <tmp>/scrapefold-crawl.md.

Drops the obsolete test_public_crawl_site_is_not_implemented test.
EOF
)"
```

---

## Phase F — Router tech debt P1 #3 + #7

Two surgical fixes documented in `docs/TECH_DEBT.md` that are cheap to land alongside the crawler since both involve sequential walks at scale.

### Task F.1 — P1 #3: per-engine `avg_response_mb` wired through `_estimate_step_cost`

**Files:**
- Modify: `src/scrapefold/router.py`
- Modify: `tests/test_router.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_router.py`:

```python
# ---------------------------------------------------------------------------
# 14. Per-engine avg_response_mb overrides the router default
# ---------------------------------------------------------------------------


async def test_router_uses_engine_avg_response_mb_override(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step whose engine declares avg_response_mb_estimate must use that
    value (not the router default) when estimating step cost."""
    from scrapefold import EngineCapabilities, SequentialStep
    from scrapefold.engines import _REGISTRY
    from scrapefold.engines.base import ScrapeEngine
    from scrapefold.options import ScrapeOptions
    from scrapefold.router import walk

    captured_mb: list[float] = []

    # Build a stub engine that declares avg_response_mb_estimate=20.0 (browser-class)
    async def _fetch(self: ScrapeEngine, url: str, opts: ScrapeOptions):
        from scrapefold.result import ScrapeResult

        return ScrapeResult(
            url=url, text="ok", markdown="# ok", html=None, engine="big_browser",
            elapsed_ms=1,
        )

    big_engine = type(
        "_StubBig",
        (ScrapeEngine,),
        {
            "NAME": "big_browser",
            "CAPABILITIES": EngineCapabilities(avg_response_mb_estimate=20.0),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch,
        },
    )
    monkeypatch.setitem(_REGISTRY, "big_browser", lambda: big_engine)

    # Patch _estimate_step_cost so we can see what avg_response_mb arrived
    import scrapefold.ladders as ladders_mod

    original = ladders_mod.estimate_step_cost

    def _spy(step, *, avg_response_mb=2.0):
        captured_mb.append(avg_response_mb)
        return original(step, avg_response_mb=avg_response_mb)

    monkeypatch.setattr(ladders_mod, "estimate_step_cost", _spy)
    # Also patch the import inside router
    import scrapefold.router as router_mod
    monkeypatch.setattr(router_mod, "estimate_step_cost", _spy)

    stub_ladder((SequentialStep(engine="big_browser", estimated_cost_usd=0.01),))

    await walk("https://example.com/")

    assert captured_mb, "estimate_step_cost was never called"
    assert captured_mb[0] == 20.0, (
        f"expected per-engine override 20.0, got {captured_mb[0]}"
    )
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_router.py::test_router_uses_engine_avg_response_mb_override -v`
Expected: FAIL — captured `2.0` (the default), not `20.0`.

- [ ] **Step 3: Patch the router**

In `src/scrapefold/router.py`, locate the section where `step_cost` is calculated. Modify the per-step cost call to consult the engine's `avg_response_mb_estimate`:

```python
        # Find this line in the existing walk() loop:
        step_cost = estimate_step_cost(step, avg_response_mb=avg_response_mb)

        # Replace with:
        engine_mb = getattr(
            engine_cls.CAPABILITIES, "avg_response_mb_estimate", avg_response_mb
        )
        step_cost = estimate_step_cost(step, avg_response_mb=engine_mb)
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_router.py::test_router_uses_engine_avg_response_mb_override -v`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

```bash
./scripts/check.sh
git add src/scrapefold/router.py tests/test_router.py
git commit -m "$(cat <<'EOF'
feat: Pack 4 — router uses per-engine avg_response_mb_estimate (P1 #3)

estimate_step_cost is now called with the engine's
CAPABILITIES.avg_response_mb_estimate (browser engines: 20.0 MB) instead
of the router-wide default 2.0. Closes TECH_DEBT P1 #3.
EOF
)"
```

### Task F.2 — P1 #7: Module-level probe cache

**Files:**
- Modify: `src/scrapefold/router.py`
- Modify: `tests/test_router.py`

- [ ] **Step 1: Failing test**

Append to `tests/test_router.py`:

```python
# ---------------------------------------------------------------------------
# 15. Probe results are cached per-domain across walks (50-URL crawl = 1 probe)
# ---------------------------------------------------------------------------


async def test_router_caches_per_domain_probe_across_urls(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scrapefold import EngineCapabilities, SequentialStep
    from scrapefold.engines import _REGISTRY
    from scrapefold.engines.base import ScrapeEngine
    from scrapefold.options import ScrapeOptions
    from scrapefold.result import ScrapeResult
    from scrapefold.router import _PROBE_CACHE, walk

    _PROBE_CACHE.clear()  # isolation

    probe_calls: list[str] = []

    async def _probe(self: ScrapeEngine, url: str) -> bool:
        probe_calls.append(url)
        return True

    async def _fetch(self: ScrapeEngine, url: str, opts: ScrapeOptions):
        return ScrapeResult(
            url=url, text="ok", markdown="# ok", html=None,
            engine="probe_engine", elapsed_ms=1,
        )

    cached_engine = type(
        "_StubCached",
        (ScrapeEngine,),
        {
            "NAME": "probe_engine",
            "CAPABILITIES": EngineCapabilities(),
            "SUPPORTED_OPTIONS": frozenset(),
            "PROBE_SCOPE": "per_domain",
            "probe": _probe,
            "_fetch": _fetch,
        },
    )
    monkeypatch.setitem(_REGISTRY, "probe_engine", lambda: cached_engine)
    stub_ladder((SequentialStep(engine="probe_engine"),))

    # Crawl two URLs on the same domain
    await walk("https://reddit.com/r/python")
    await walk("https://reddit.com/r/programming")

    assert len(probe_calls) == 1, f"expected 1 probe per domain, got {len(probe_calls)}"
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_router.py::test_router_caches_per_domain_probe_across_urls -v`
Expected: AttributeError (`_PROBE_CACHE` doesn't exist).

- [ ] **Step 3: Implement probe cache in router**

In `src/scrapefold/router.py`, near the top after imports, add:

```python
import tldextract  # already a project dep

_PROBE_CACHE: dict[tuple[str, str], bool] = {}


def _probe_scope_key(engine_cls: type, url: str) -> tuple[str, str] | None:
    """Return the (engine_name, scope_key) cache key, or None if scope=='none'."""
    scope = getattr(engine_cls, "PROBE_SCOPE", "none")
    if scope == "none":
        return None
    if scope == "per_url":
        return (engine_cls.NAME, url)
    if scope == "per_domain":
        ext = tldextract.extract(url)
        domain = f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain
        return (engine_cls.NAME, domain)
    if scope == "per_session":
        return (engine_cls.NAME, "_session")
    return None
```

Then in the `walk()` loop, after `engine = engine_cls()` and before `await engine.scrape(...)`, insert:

```python
        # Probe cache — skip the engine if a prior probe returned False
        cache_key = _probe_scope_key(engine_cls, url)
        if cache_key is not None:
            cached = _PROBE_CACHE.get(cache_key)
            if cached is False:
                logger.debug("router: probe cache says skip engine=%s for %s", canonical, url)
                failures.append(f"{canonical}:probe_cache_skip")
                continue
            if cached is None and hasattr(engine, "probe"):
                ok = await engine.probe(url)
                _PROBE_CACHE[cache_key] = ok
                if not ok:
                    failures.append(f"{canonical}:probe_failed")
                    continue
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_router.py::test_router_caches_per_domain_probe_across_urls -v`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

```bash
./scripts/check.sh
git add src/scrapefold/router.py tests/test_router.py
git commit -m "$(cat <<'EOF'
feat: Pack 4 — module-level probe cache (P1 #7)

_PROBE_CACHE keyed by (engine_name, scope_key) where scope_key is the
URL itself / registered domain / "_session" depending on the engine's
PROBE_SCOPE. A 50-URL crawl on reddit.com now costs one probe per
engine, not 50. Closes TECH_DEBT P1 #7.
EOF
)"
```

---

## Phase G — CHANGELOG roll + tag

### Task G.1 — Bump version

- [ ] **Step 1: Edit `__version__`**

In `src/scrapefold/__init__.py`:

```python
__version__ = "0.1.0a3"
```

- [ ] **Step 2: Roll CHANGELOG**

Move all Pack 4 `[Unreleased]` entries into `[0.1.0a3] — <date>`, open a new empty `[Unreleased]`, add compare-link line.

- [ ] **Step 3: Full suite**

Run: `./scripts/check.sh`
Expected: `=== Version equality === OK: 0.1.0a3` and `=== All checks passed ===`.

- [ ] **Step 4: Commit + ASK before push**

```bash
git add src/scrapefold/__init__.py CHANGELOG.md docs/TECH_DEBT.md
git commit -m "chore: bump version to 0.1.0a3 — Pack 4 release"
```

**Stop here.** Ask Mike to confirm push + tag (`v0.1.0a3`). If approved: push, tag, push tag, watch CI.

### Task G.2 — Update TECH_DEBT.md

- [ ] **Step 1: Remove items 3 and 7 from `docs/TECH_DEBT.md`**

Delete the entire "### 3." and "### 7." entries from the P1 section. Add a one-line note in `CHANGELOG.md` `[0.1.0a3]` § Changed:

```markdown
- TECH_DEBT register: P1 #3 (per-engine avg_response_mb) and P1 #7 (probe
  cache) closed; entries removed from `docs/TECH_DEBT.md`.
```

---

## Self-review

**Spec coverage** (§3.2):
- sitemap.py → Phase C ✅
- filters.py → Phase B ✅
- stitcher.py → Phase D ✅
- crawl_site public API → Phase E ✅
- P1 #3 wired → Phase F.1 ✅
- P1 #7 probe cache → Phase F.2 ✅
- ~30-50 new tests: 16 (filters) + 6 (sitemap) + 5 (stitcher) + 3 (crawl_site) + 2 (router) = 32 ✅
- Exit: `crawl_site(...)` returns a Path; offline tests use fixture sitemaps → Phase E.1 fixtures ✅

**No placeholders.** Each step has complete code.

**Type consistency.**
- `discover_urls(root, *, max_urls)` keyword-only on `max_urls` — consistent across signature, test calls, and orchestrator.
- `write_stitched(results, output)` returns `Path` — pinned by `test_returns_path`.
- `crawl(root, opts, output)` and `crawl_site(url, opts, output)` use the same param names; alias of `url`/`root` is intentional and tested.

**Deferred:** P1 #1 (`budget_mode`), P1 #2 (race billing), P2 #8/#9 (engine client reuse → Pack 5).
