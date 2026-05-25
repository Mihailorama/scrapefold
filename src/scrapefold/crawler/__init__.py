"""crawler — sitemap discovery + filtering + stitching.

Public entry point: ``async def crawl(root, opts, output) -> CrawlResult``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import httpx

import scrapefold.crawler.sitemap as sitemap
from scrapefold._host_utils import _is_invalid_location_error
from scrapefold._host_utils import same_host as _same_host
from scrapefold.cache import Cache, make_key
from scrapefold.crawler.result import CrawlResult
from scrapefold.crawler.stitcher import write_stitched
from scrapefold.options import ScrapeOptions
from scrapefold.pool import EnginePool
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
    except httpx.RemoteProtocolError as exc:
        # httpx parses the Location header even on follow_redirects=False;
        # a malformed Location (e.g. unterminated IPv6 bracket) raises
        # RemoteProtocolError BEFORE we can inspect the response.
        if _is_invalid_location_error(exc):
            logger.debug(
                "crawler: preflight HEAD raised malformed-Location error for %s — skipping url: %s",
                url,
                exc,
            )
            return False  # URL is unsafe to crawl — malformed redirect Location
        logger.debug("crawler: preflight HEAD transient protocol error url=%s err=%s", url, exc)
        return True  # transient protocol failure — let the engine layer try
    except httpx.HTTPError as exc:
        logger.debug("crawler: preflight HEAD failed url=%s err=%s", url, exc)
        # Cannot confirm safety; let the scrape engine decide
        return True

    if resp.status_code not in _REDIRECT_STATUS:
        return True  # 2xx / 4xx / 5xx — proceed to scrape

    location = resp.headers.get("location")
    if not location:
        return True  # malformed redirect — let scrape engine handle it

    try:
        target = urljoin(url, location)
    except ValueError:
        logger.debug(
            "crawler: malformed Location header %r in preflight for %s — skipping url",
            location,
            url,
        )
        return False
    if _same_host(target, root, follow_subdomains):
        return True  # same-host redirect — safe

    logger.warning("crawler: skipping url=%s — redirects off-host to %s", url, target)
    return False


async def crawl(
    root: str,
    opts: ScrapeOptions | None = None,
    output: Path | str | None = None,
) -> CrawlResult:
    """Walk a site → produce one stitched markdown file.

    Discovery: sitemap → robots → BFS (see ``crawler.sitemap``).
    Per-URL fetch delegates to ``scrapefold.scrape`` after a lightweight
    HEAD pre-flight that rejects scrape-time off-host redirects (SSRF guard).
    Output: ``crawler.stitcher.write_stitched``.

    Pass an explicit ``output=`` for a predictable output path; omitting it
    allocates a unique temp file via :func:`_default_output_path`.

    Returns a :class:`~scrapefold.crawler.result.CrawlResult` with the
    per-URL pages, the stitched markdown path, and per-URL failure strings.

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

    **Cache integration**

    If ``opts.extra["cache_dir"]`` is set, a :class:`~scrapefold.cache.Cache`
    is consulted before each per-URL scrape and populated after each success.
    ``opts.skip_cache=True`` bypasses both read and write.
    """
    from dataclasses import replace as _replace

    from scrapefold import scrape  # local import — avoids circular

    opts = opts or ScrapeOptions()
    max_pages = opts.max_pages if opts.max_pages is not None else 100
    if max_pages <= 0:
        if output is None:
            output = _default_output_path()
        output_path = Path(output)
        write_stitched([], output_path)
        return CrawlResult(pages=(), stitched_path=output_path, failures=())

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

    cache: Cache | None = None
    cache_dir_raw = opts.extra.get("cache_dir")
    if cache_dir_raw is not None:
        ttl_days = int(opts.extra.get("cache_ttl_days", 7))
        cache = Cache(dir=Path(str(cache_dir_raw)), ttl_days=ttl_days)

    pool = EnginePool()
    results: list[ScrapeResult] = []
    failures: list[str] = []
    follow_subdomains = opts.follow_subdomains
    try:
        async with httpx.AsyncClient(timeout=10.0) as head_client:
            for url in urls:
                if not await _preflight_head(head_client, url, root, follow_subdomains):
                    logger.info("crawler: url skipped (redirect_offhost) url=%s", url)
                    continue

                cache_key = make_key(url, crawl_opts) if cache is not None else None
                if cache is not None and cache_key is not None:
                    cached = await cache.get(cache_key, opts=opts)
                    if cached is not None:
                        logger.debug("crawler: cache hit url=%s", url)
                        results.append(cached)
                        continue

                try:
                    result = await scrape(url, crawl_opts, pool=pool)
                except Exception as exc:  # broad — per-URL failures must not abort the crawl
                    logger.warning("crawler: scrape failed url=%s err=%s", url, exc)
                    failures.append(f"{url}:{type(exc).__name__}:{exc}")
                    continue

                results.append(result)
                if cache is not None and cache_key is not None:
                    await cache.set(cache_key, result, opts=opts)
    finally:
        await pool.aclose()

    if output is None:
        output = _default_output_path()
    output_path = Path(output)

    write_stitched(results, output_path)
    return CrawlResult(
        pages=tuple(results),
        stitched_path=output_path,
        failures=tuple(failures),
    )


__all__ = ["crawl"]
