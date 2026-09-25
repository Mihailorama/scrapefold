"""Compatibility imports for Enrichfold-owned web search.

Install Scrapefold with Enrichfold to use search; scraping remains local.
"""

from enrichfold.search import (
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
