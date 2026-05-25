"""scrapefold — unified interface for web scraping engines.

Public API (v0.1 — scaffold; engines land in later PRs):

    from scrapefold import scrape, crawl_site, ScrapeOptions, ScrapeResult, ScrapeEngine

    res = await scrape("https://example.com")
    res = await scrape(url, opts=ScrapeOptions(language="ru", stealth=True))
    md_path = await crawl_site("https://docs.example.com", opts=ScrapeOptions(max_pages=50))
"""

from __future__ import annotations

from scrapefold.crawler.result import CrawlResult
from scrapefold.engines.base import (
    EngineCapabilities,
    EngineError,
    RedirectScopeViolation,
    ScrapeEngine,
)
from scrapefold.ladders import (
    AllEnginesFailed,
    BudgetExceeded,
    Policy,
    RaceStep,
    SequentialStep,
    SiteClass,
    WalkBudget,
    classify_url,
    get_ladder,
)
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult
from scrapefold.router import walk as _walk

__version__ = "0.1.0a2"

__all__ = [
    "AllEnginesFailed",
    "BudgetExceeded",
    "CrawlResult",
    "EngineCapabilities",
    "EngineError",
    "Policy",
    "RaceStep",
    "RedirectScopeViolation",
    "ScrapeEngine",
    "ScrapeOptions",
    "ScrapeResult",
    "SequentialStep",
    "SiteClass",
    "WalkBudget",
    "__version__",
    "classify_url",
    "crawl_site",
    "get_ladder",
    "scrape",
]


async def scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
    """Single-URL scrape with engine auto-selection.

    Walks the per-site-class ladder via ``scrapefold.router.walk``. Raises
    ``AllEnginesFailed`` if no step in the ladder succeeds.
    """
    return await _walk(url, opts)


async def crawl_site(
    url: str,
    opts: ScrapeOptions | None = None,
    output: object | None = None,
    **_unused: object,
) -> CrawlResult:
    """Whole-site crawl → single markdown file.

    Discovers URLs from ``url`` (sitemap → robots → BFS), scrapes each
    via ``scrape()``, and writes a stitched .md file at ``output``
    (defaults to a unique temp path under ``/tmp``). Returns a
    :class:`CrawlResult` with the per-URL pages, the stitched markdown
    path, and per-URL failure strings.

    Pass ``opts.extra["cache_dir"]`` to enable disk caching. Set
    ``opts.skip_cache=True`` to bypass the cache for both reads and writes.

    Unknown keyword arguments (e.g. ``cache_ttl_hours``) are accepted and
    silently ignored for forward compatibility.
    """
    import logging as _logging

    if _unused:
        _logging.getLogger(__name__).debug(
            "crawl_site: ignoring unimplemented kwargs: %s", sorted(_unused.keys())
        )
    from scrapefold.crawler import crawl

    return await crawl(url, opts=opts, output=output)  # type: ignore[arg-type]
