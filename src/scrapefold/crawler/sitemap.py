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
        nested = [
            el.text.strip() for el in root.iter(f"{_SITEMAP_NS}loc") if isinstance(el.text, str)
        ]
        return [], nested

    if root.tag.endswith("urlset"):
        pages = [
            el.text.strip() for el in root.iter(f"{_SITEMAP_NS}loc") if isinstance(el.text, str)
        ]
        return pages, []

    return [], []


def _parse_robots_for_sitemaps(text: str) -> list[str]:
    return [m.group(1).strip() for m in _ROBOTS_SITEMAP_RE.finditer(text)]


def _bfs_links_from_html(html: str, *, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        raw_href = a["href"]
        href = (raw_href if isinstance(raw_href, str) else str(raw_href)).strip()
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


async def _walk_sitemap(client: httpx.AsyncClient, sitemap_url: str, found: list[str]) -> None:
    """Fetch *sitemap_url* and recurse into nested sitemap-index entries."""
    resp = await _fetch(client, sitemap_url)
    if resp is None or resp.status_code != 200:
        return
    pages, nested = _parse_sitemap(resp.text)
    found.extend(pages)
    for n in nested:
        await _walk_sitemap(client, n, found)


__all__ = ["discover_urls"]
