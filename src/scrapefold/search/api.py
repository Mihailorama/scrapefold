"""Public ``search()`` entrypoint — fan out to engines, fuse with RRF.

``search(query)`` queries every available search engine concurrently and merges
their ranked lists with Reciprocal Rank Fusion, producing explainable
per-result scores. Engines that error are logged and skipped; the search only
fails if *every* engine fails.
"""

from __future__ import annotations

import asyncio
import logging

from scrapefold.search.engines import get_search_engine, list_search_engine_names
from scrapefold.search.engines.base import SearchEngine, SearchEngineError
from scrapefold.search.fusion import reciprocal_rank_fusion
from scrapefold.search.options import SearchOptions
from scrapefold.search.types import SearchHit, SearchResult

logger = logging.getLogger(__name__)

_KEYLESS_FALLBACK: tuple[str, ...] = ("duckduckgo",)


def _instantiate(name: str) -> SearchEngine | None:
    """Return an engine instance for ``name``, or ``None`` if the name is unknown."""
    try:
        return get_search_engine(name)()
    except KeyError:
        logger.warning("unknown search engine %r, skipping", name)
        return None


def _default_engines() -> list[SearchEngine]:
    """Every registered engine whose instance reports itself available."""
    engines: list[SearchEngine] = []
    for name in list_search_engine_names():
        engine = _instantiate(name)
        if engine is not None and engine.is_available():
            engines.append(engine)
    return engines


def _select_engines(opts: SearchOptions) -> list[SearchEngine]:
    if opts.engines:
        selected = [engine for name in opts.engines if (engine := _instantiate(name)) is not None]
    else:
        selected = _default_engines()
    if not selected:
        selected = [engine for name in _KEYLESS_FALLBACK if (engine := _instantiate(name))]
    return selected


async def search(query: str, opts: SearchOptions | None = None) -> list[SearchResult]:
    """Search across multiple engines concurrently and RRF-fuse the results."""
    opts = opts or SearchOptions()
    engines = _select_engines(opts)

    outcomes = await asyncio.gather(
        *(engine.search(query, opts) for engine in engines),
        return_exceptions=True,
    )

    per_engine: dict[str, list[SearchHit]] = {}
    for engine, outcome in zip(engines, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning("search engine %s failed: %s", engine.NAME, outcome)
            continue
        per_engine[engine.NAME] = outcome

    if not per_engine:
        raise SearchEngineError("search", "all search engines failed")

    fused = reciprocal_rank_fusion(per_engine, k=opts.rrf_k)
    return fused[: opts.count]


__all__ = ["search"]
