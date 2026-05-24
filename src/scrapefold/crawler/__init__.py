"""crawler — sitemap discovery + filtering + stitching.

Public entry point: ``async def crawl(root, opts, output) -> Path``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import httpx

import scrapefold.crawler.sitemap as sitemap
from scrapefold._host_utils import same_host as _same_host
from scrapefold.crawler.stitcher import write_stitched
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def _default_output_path() -> Path:
    """Allocate a unique temp file for crawl output.

    Callers should pass an explicit ``output=`` parameter for predictable paths.
    Using mkstemp avoids both concurrent-crawl clobbering and symlink-race
    attacks on shared temp directories.
    """
    fd, path = tempfile.mkstemp(prefix="scrapefold-crawl-", suffix=".md")
    os.close(fd)  # mkstemp returns an open fd; we just want the path
    return Path(path)


async def _preflight_head(
    client: httpx.AsyncClient,
    url: str,
    root: str,
    follow_subdomains: bool,
) -> bool:
    """HEAD *url* without following redirects; return True if safe to scrape.

    Returns False (skip the URL) when the server issues a 3xx redirect to an
    off-host target.  Returns True for 2xx, 4xx, 5xx, and same-host redirects.

    NOTE: HEAD and GET can return different status codes on some servers
    (e.g. a HEAD 405 does not mean GET will fail).  This check is conservative:
    it only blocks off-host redirects; it never blocks non-redirect responses.
    """
    try:
        resp = await client.head(url, follow_redirects=False)
    except httpx.HTTPError as exc:
        logger.debug("crawler: preflight HEAD failed url=%s err=%s", url, exc)
        # Cannot confirm safety; let the scrape engine decide
        return True

    if resp.status_code not in _REDIRECT_STATUS:
        return True  # 2xx / 4xx / 5xx — proceed to scrape

    location = resp.headers.get("location")
    if not location:
        return True  # malformed redirect — let scrape engine handle it

    target = urljoin(url, location)
    if _same_host(target, root, follow_subdomains):
        return True  # same-host redirect — safe

    logger.warning("crawler: skipping url=%s — redirects off-host to %s", url, target)
    return False


async def crawl(
    root: str,
    opts: ScrapeOptions | None = None,
    output: Path | str | None = None,
) -> Path:
    """Walk a site → produce one stitched markdown file.

    Discovery: sitemap → robots → BFS (see ``crawler.sitemap``).
    Per-URL fetch delegates to ``scrapefold.scrape`` after a lightweight
    HEAD pre-flight that rejects scrape-time off-host redirects (SSRF guard).
    Output: ``crawler.stitcher.write_stitched``.

    Pass an explicit ``output=`` for a predictable output path; omitting it
    allocates a unique temp file via :func:`_default_output_path`.

    **SSRF protection**

    ``crawl`` injects ``opts.extra["same_host_redirect_scope"]`` into the
    per-URL options so the ``requests`` engine raises
    :class:`~scrapefold.engines.base.RedirectScopeViolation` on any off-host
    redirect.  The router catches this exception and *terminates the walk* for
    that URL — it does **not** escalate to other engines, which would simply
    follow the redirect on their own backend.

    **Scope-aware**: ``requests`` engine only.

    **NOT scope-aware**: vendor engines (firecrawl, scrapingbee, cloudflare,
    jina, brightdata) and local stealth engines (scrapling, crawl4ai,
    cloakbrowser, selenium).  If invoked directly via
    ``opts.engines=("firecrawl",)`` or reached via the default escalation
    ladder, they may follow off-host redirects on their backend without raising.
    For SSRF-sensitive deployments embedded in services, restrict engine
    selection with ``opts.engines=("requests",)`` in ``crawl_site`` calls, or
    add network-layer egress controls.
    """
    from dataclasses import replace as _replace

    from scrapefold import scrape  # local import — avoids circular

    opts = opts or ScrapeOptions()
    max_pages = opts.max_pages if opts.max_pages is not None else 100
    if max_pages <= 0:
        if output is None:
            output = _default_output_path()
        return write_stitched([], Path(output))

    urls = await sitemap.discover_urls(
        root,
        max_urls=max_pages,
        max_depth=opts.max_depth,
        follow_subdomains=opts.follow_subdomains,
    )
    logger.info("crawler: discovered %d urls from %s", len(urls), root)

    if not urls:
        urls = [root]  # at least try the root

    # Build a derived options object that carries the redirect-scope guard.
    # We do NOT mutate the caller's opts — use dataclasses.replace to fork.
    crawl_extra = {
        **opts.extra,
        "same_host_redirect_scope": {
            "root": root,
            "follow_subdomains": opts.follow_subdomains,
        },
    }
    crawl_opts = _replace(opts, extra=crawl_extra)

    results: list[ScrapeResult] = []
    follow_subdomains = opts.follow_subdomains
    async with httpx.AsyncClient(timeout=10.0) as head_client:
        for url in urls:
            if not await _preflight_head(head_client, url, root, follow_subdomains):
                logger.info("crawler: url skipped (redirect_offhost) url=%s", url)
                continue
            try:
                results.append(await scrape(url, crawl_opts))
            except Exception as exc:  # broad — per-URL failures must not abort the crawl
                logger.warning("crawler: scrape failed url=%s err=%s", url, exc)

    if output is None:
        output = _default_output_path()
    output = Path(output)

    return write_stitched(results, output)


__all__ = ["crawl"]
