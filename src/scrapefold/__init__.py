"""scrapefold — unified interface for web scraping engines.

Public API:

    from scrapefold import scrape, crawl_site, search, ScrapeOptions, ScrapeResult

    res = await scrape("https://example.com")
    res = await scrape(url, opts=ScrapeOptions(language="ru", stealth=True))
    md_path = await crawl_site("https://docs.example.com", opts=ScrapeOptions(max_pages=50))
    data = await extract(res, schema={...}, llm=my_llm)  # your LLM callable, no vendor SDK

    # Multi-engine web search with Reciprocal Rank Fusion + explainable scoring:
    hits = await search("agentic document processing")   # query -> ranked results

    # Ground extracted values back to source spans (verify, don't trust):
    cites = find_citations(res, res.json)

    # Detect what changed between two scrapes of one URL:
    diff = await check_for_changes(url, store=SnapshotStore("~/.scrapefold/snapshots"))
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, TypeVar

from scrapefold.citations import (
    Citation,
    CitationReport,
    Span,
    cite_result,
    find_citations,
)
from scrapefold.crawler.result import CrawlResult
from scrapefold.diff import (
    ContentDiff,
    SnapshotStore,
    check_for_changes,
    diff_results,
    diff_text,
)
from scrapefold.engines.base import (
    EngineCapabilities,
    EngineError,
    RedirectScopeViolation,
    ScrapeEngine,
)
from scrapefold.extract import ExtractionError, TextLLMCallable, extract, extract_into
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
from scrapefold.pool import EnginePool
from scrapefold.result import ScrapeResult
from scrapefold.router import walk as _walk
from scrapefold.search import (
    SearchEngine,
    SearchEngineError,
    SearchHit,
    SearchOptions,
    SearchResult,
    fuse,
    get_search_engine,
    list_search_engine_names,
    reciprocal_rank_fusion,
    search,
)
from scrapefold.social import (
    Author,
    Comment,
    Media,
    Post,
    Profile,
    SocialEntity,
    normalize_social,
)

__version__ = "0.10.0"

__all__ = [
    "AllEnginesFailed",
    "Author",
    "BudgetExceeded",
    "Citation",
    "CitationReport",
    "Comment",
    "ContentDiff",
    "CrawlResult",
    "EngineCapabilities",
    "EngineError",
    "EnginePool",
    "ExtractionError",
    "Media",
    "Policy",
    "Post",
    "Profile",
    "RaceStep",
    "RedirectScopeViolation",
    "ScrapeEngine",
    "ScrapeOptions",
    "ScrapeResult",
    "SearchEngine",
    "SearchEngineError",
    "SearchHit",
    "SearchOptions",
    "SearchResult",
    "SequentialStep",
    "SiteClass",
    "SnapshotStore",
    "SocialEntity",
    "Span",
    "TextLLMCallable",
    "WalkBudget",
    "__version__",
    "check_for_changes",
    "cite_result",
    "classify_url",
    "crawl_site",
    "crawl_site_sync",
    "diff_results",
    "diff_text",
    "extract",
    "extract_into",
    "find_citations",
    "fuse",
    "get_ladder",
    "get_search_engine",
    "list_search_engine_names",
    "normalize_social",
    "reciprocal_rank_fusion",
    "scrape",
    "scrape_sync",
    "search",
]


async def scrape(
    url: str,
    opts: ScrapeOptions | None = None,
    pool: EnginePool | None = None,
) -> ScrapeResult:
    """Single-URL scrape with engine auto-selection.

    Walks the per-site-class ladder via ``scrapefold.router.walk``. Raises
    ``AllEnginesFailed`` if no step in the ladder succeeds.

    Pass a caller-owned ``pool`` to reuse engine instances across scrape calls
    (e.g. during a ``crawl_site`` run). When ``None`` (default), an ephemeral
    pool is created and closed for each call.
    """
    return await _walk(url, opts, pool=pool)


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

    Pass ``opts.extra["cache_dir"]`` to enable disk caching. The cache
    TTL is read from ``opts.extra["cache_ttl_days"]`` (default: 7 days).
    Set ``opts.skip_cache=True`` to bypass the cache for both reads and writes.

    Unknown keyword arguments are accepted and silently ignored for
    forward compatibility (logged at DEBUG).
    """
    import logging as _logging

    if _unused:
        _logging.getLogger(__name__).debug(
            "crawl_site: ignoring unimplemented kwargs: %s", sorted(_unused.keys())
        )
    from scrapefold.crawler import crawl

    return await crawl(url, opts=opts, output=output)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sync wrappers — safe under leaked event loops in the caller (TECH_DEBT #12)
# ---------------------------------------------------------------------------

if TYPE_CHECKING:
    from collections.abc import Coroutine

_T = TypeVar("_T")


def _run_sync(coro: Coroutine[object, object, _T]) -> _T:
    """Run *coro* to completion on a fresh event loop in a dedicated worker thread.

    A bare ``asyncio.run(coro)`` in the caller's thread explodes with
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``
    the moment the process has *any* loop leaked into the main thread — most
    commonly by the Playwright Sync API. A single-use worker thread always has
    a clean asyncio context, so the wrapper works regardless of the caller's
    loop state. (Found in the wild by phynder's adapter, Mihailorama/phynder#62.)
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="scrapefold-sync") as executor:
        return executor.submit(asyncio.run, coro).result()


def scrape_sync(
    url: str,
    opts: ScrapeOptions | None = None,
) -> ScrapeResult:
    """Blocking :func:`scrape` for sync codebases.

    Runs the async walk on a fresh event loop in a dedicated worker thread, so
    it keeps working even when the calling thread already has a (possibly
    leaked) running event loop — the case where ``asyncio.run(scrape(...))``
    raises ``RuntimeError``.

    Unlike :func:`scrape`, there is no ``pool`` parameter: each call runs in
    its own short-lived event loop, and an :class:`EnginePool` holds network
    clients bound to the loop they were created on — reusing one across calls
    would hand out clients tied to a dead loop. Sync callers who need
    connection reuse across many URLs should use :func:`crawl_site_sync`
    (one loop for the whole crawl) or the async API.
    """
    return _run_sync(scrape(url, opts))


def crawl_site_sync(
    url: str,
    opts: ScrapeOptions | None = None,
    output: object | None = None,
    **kwargs: object,
) -> CrawlResult:
    """Blocking :func:`crawl_site` for sync codebases.

    Same worker-thread isolation as :func:`scrape_sync`; the whole crawl —
    discovery, per-page scrapes, engine-pool reuse, stitching — runs inside
    one fresh event loop and the finished :class:`CrawlResult` is returned.
    """
    return _run_sync(crawl_site(url, opts, output, **kwargs))
