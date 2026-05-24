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

from scrapefold._host_utils import same_host as _same_host
from scrapefold.crawler.filters import filter_urls, is_crawlable

logger = logging.getLogger(__name__)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*Sitemap:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)


_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECT_HOPS = 5


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    root: str | None = None,
    follow_subdomains: bool = False,
) -> httpx.Response | None:
    """GET *url* with optional same-host redirect validation.

    When *root* is provided every redirect hop is validated against the same
    host as *root* before following it.  An off-host redirect target is NOT
    followed; the 3xx response itself is returned so callers see a non-200
    status and treat it as a missing resource (safe degradation).

    When *root* is ``None`` the function still refuses to follow redirects
    automatically — it returns the first response regardless of status,
    keeping behaviour consistent and predictable.
    """
    current = url
    for _hop in range(_MAX_REDIRECT_HOPS):
        try:
            resp = await client.get(current, follow_redirects=False)
        except httpx.HTTPError as exc:
            logger.debug("sitemap: GET %s failed: %s", current, exc)
            return None
        if resp.status_code not in _REDIRECT_STATUS:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        target = urljoin(current, location)
        if root is not None and not _same_host(target, root, follow_subdomains):
            logger.debug(
                "sitemap: redirect to off-host target rejected (root=%s target=%s)",
                root,
                target,
            )
            return resp  # return 3xx; caller treats non-200 as missing
        current = target
    logger.debug("sitemap: too many redirects for %s", url)
    return None


def _local_name(tag: str) -> str:
    """Strip Clark-notation namespace prefix from an XML tag name.

    ``{http://www.sitemaps.org/schemas/sitemap/0.9}loc`` → ``loc``
    ``loc`` → ``loc``
    """
    return tag.rpartition("}")[2] if "}" in tag else tag


def _parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, nested_sitemap_urls) from a sitemap XML body.

    Handles both namespace-qualified XML (``xmlns="http://www.sitemaps.org/…"``)
    and namespace-less XML — matching is done by local element name so both
    forms are accepted transparently.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("sitemap: XML parse failed: %s", exc)
        return [], []

    root_local = _local_name(root.tag)

    if root_local == "sitemapindex":
        nested = [
            el.text.strip()
            for el in root.iter()
            if _local_name(el.tag) == "loc" and isinstance(el.text, str)
        ]
        return [], nested

    if root_local == "urlset":
        pages = [
            el.text.strip()
            for el in root.iter()
            if _local_name(el.tag) == "loc" and isinstance(el.text, str)
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


async def discover_urls(
    root: str,
    *,
    max_urls: int = 100,
    max_depth: int = 3,
    follow_subdomains: bool = False,
) -> list[str]:
    """Discover same-host crawlable URLs from *root*, capped at *max_urls*.

    Tries `<root>/sitemap.xml`, then `<root>/robots.txt` Sitemap directives,
    then BFS from the root page's links. Returns filtered, deduped,
    discovery-order URLs.

    *follow_subdomains* is forwarded to :func:`~scrapefold.crawler.filters.filter_urls`.
    When ``False`` (default) only the root host (www/apex-equivalent) is
    accepted; when ``True`` all subdomains of the registered domain pass.

    *max_depth* controls how deep the BFS fallback walks (default 3).
    """
    if max_urls <= 0:
        return []

    parsed = urlparse(root)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = f"{origin}/sitemap.xml"
    robots_url = f"{origin}/robots.txt"

    found: list[str] = []

    visited: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Tier 1 — /sitemap.xml direct
        found = await _walk_sitemap(
            client,
            sitemap_url,
            visited=visited,
            max_urls=max_urls,
            root=root,
            follow_subdomains=follow_subdomains,
        )

        # Tier 2 — /robots.txt sitemap directives
        if not found:
            robots_resp = await _fetch(
                client, robots_url, root=root, follow_subdomains=follow_subdomains
            )
            if robots_resp is not None and robots_resp.status_code == 200:
                for nested in _parse_robots_for_sitemaps(robots_resp.text):
                    # SSRF guard: reject off-host Sitemap: directives silently
                    if not _same_host(nested, root, follow_subdomains):
                        logger.debug(
                            "sitemap: robots.txt Sitemap directive points off-host "
                            "(root=%s directive=%s) — skipping",
                            root,
                            nested,
                        )
                        continue
                    more = await _walk_sitemap(
                        client,
                        nested,
                        visited=visited,
                        max_urls=max_urls - len(found),
                        root=root,
                        follow_subdomains=follow_subdomains,
                    )
                    found.extend(more)
                    if len(found) >= max_urls:
                        break

        # Tier 3 — BFS from root page
        if not found:
            found = await _bfs_crawl(
                client,
                root,
                max_urls=max_urls,
                max_depth=max_depth,
                follow_subdomains=follow_subdomains,
            )

    filtered = filter_urls(found, root=root, follow_subdomains=follow_subdomains)
    return filtered[:max_urls]


async def _walk_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    *,
    visited: set[str],
    max_urls: int,
    root: str,
    follow_subdomains: bool,
    depth: int = 0,
    max_depth: int = 5,
) -> list[str]:
    """Fetch *sitemap_url* and recurse into nested sitemap-index entries.

    *visited* prevents infinite loops from self-referential sitemap indexes.
    *max_urls* caps total page URLs collected — the walk short-circuits once
    this budget is reached, avoiding unnecessary HTTP requests.
    *max_depth* (default 5) is a hard ceiling on recursion depth.
    *root* and *follow_subdomains* are used to validate nested sitemap URLs
    before fetching them (SSRF guard).
    """
    # Host validation BEFORE visited-set check so attacker cannot poison the
    # visited set with off-origin URLs and prevent legitimate same-host walks.
    if not _same_host(sitemap_url, root, follow_subdomains):
        logger.debug(
            "sitemap: nested sitemap URL is off-host (root=%s url=%s) — skipping",
            root,
            sitemap_url,
        )
        return []

    if sitemap_url in visited:
        return []
    if depth > max_depth:
        logger.debug("sitemap: max_depth=%d reached at %s", max_depth, sitemap_url)
        return []
    visited.add(sitemap_url)

    resp = await _fetch(client, sitemap_url, root=root, follow_subdomains=follow_subdomains)
    if resp is None or resp.status_code != 200:
        return []

    pages, nested_sitemaps = _parse_sitemap(resp.text)
    out: list[str] = list(pages)

    for child in nested_sitemaps:
        if len(out) >= max_urls:
            break
        more = await _walk_sitemap(
            client,
            child,
            visited=visited,
            max_urls=max_urls - len(out),
            root=root,
            follow_subdomains=follow_subdomains,
            depth=depth + 1,
            max_depth=max_depth,
        )
        out.extend(more)

    return out


async def _bfs_crawl(
    client: httpx.AsyncClient,
    root: str,
    *,
    max_urls: int,
    max_depth: int,
    follow_subdomains: bool,
) -> list[str]:
    """BFS crawl starting from *root*, collecting URLs up to *max_urls*.

    Fetches each page in queue order (breadth-first), extracts ``<a href>``
    links, validates them against the same-host rule, and enqueues them at
    depth+1 until the budget or depth cap is reached.

    Returns an unfiltered list — caller passes through
    :func:`~scrapefold.crawler.filters.filter_urls`.
    """
    # queue items: (url, depth)
    queue: list[tuple[str, int]] = [(root, 0)]
    visited: set[str] = set()
    found: list[str] = []

    while queue and len(found) < max_urls:
        url, depth = queue.pop(0)

        if url in visited:
            continue
        if depth > max_depth:
            continue

        visited.add(url)

        resp = await _fetch(client, url, root=root, follow_subdomains=follow_subdomains)
        if resp is None or resp.status_code != 200:
            continue

        ct = resp.headers.get("content-type", "").lower()
        if "html" not in ct and "<html" not in resp.text[:512].lower():
            continue

        found.append(url)

        if depth < max_depth:
            links = _bfs_links_from_html(resp.text, base_url=url)
            for link in links:
                if (
                    link not in visited
                    and _same_host(link, root, follow_subdomains)
                    and is_crawlable(link)
                ):
                    queue.append((link, depth + 1))

    return found


__all__ = ["discover_urls"]
