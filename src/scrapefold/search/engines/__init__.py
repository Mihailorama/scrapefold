"""Compatibility search engine registry; implementation lives in Enrichfold."""

from enrichfold.search.engines import (
    SEARCH_ENGINE_ALIASES,
    get_search_engine,
    list_search_engine_names,
    register,
    register_alias,
    resolve_alias,
)

__all__ = [
    "SEARCH_ENGINE_ALIASES",
    "get_search_engine",
    "list_search_engine_names",
    "register",
    "register_alias",
    "resolve_alias",
]
