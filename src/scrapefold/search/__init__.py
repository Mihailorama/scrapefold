"""Multi-engine web search with Reciprocal Rank Fusion.

``search(query)`` fans out to several SERP engines concurrently and merges their
ranked lists with RRF, producing deduped results with explainable per-engine
score breakdowns. This is a parallel subsystem to ``scrapefold.scrape`` /
``crawl_site`` — query in, ranked results out.
"""

from __future__ import annotations

from scrapefold.search.api import search
from scrapefold.search.engines import get_search_engine, list_search_engine_names
from scrapefold.search.engines.base import SearchEngine, SearchEngineError
from scrapefold.search.fusion import fuse, reciprocal_rank_fusion
from scrapefold.search.options import SearchOptions
from scrapefold.search.types import SearchHit, SearchResult

__all__ = [
    "SearchEngine",
    "SearchEngineError",
    "SearchHit",
    "SearchOptions",
    "SearchResult",
    "fuse",
    "get_search_engine",
    "list_search_engine_names",
    "reciprocal_rank_fusion",
    "search",
]
