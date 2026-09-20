"""Search engine registry.

Engines are imported lazily so that a missing key or extra never breaks import
of ``scrapefold``. ``get_search_engine(name)`` returns a class on demand. This
is a separate registry from the scrape engines in ``scrapefold.engines``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapefold.search.engines.base import SearchEngine

# Lazy registry: name -> import-and-return-class function.
_REGISTRY: dict[str, Callable[[], type[SearchEngine]]] = {
    "serper": lambda: (
        __import__(
            "scrapefold.search.engines.serper_search", fromlist=["SerperSearchEngine"]
        ).SerperSearchEngine
    ),
    "exa": lambda: (
        __import__(
            "scrapefold.search.engines.exa_search", fromlist=["ExaSearchEngine"]
        ).ExaSearchEngine
    ),
    "duckduckgo": lambda: (
        __import__(
            "scrapefold.search.engines.duckduckgo", fromlist=["DuckDuckGoSearchEngine"]
        ).DuckDuckGoSearchEngine
    ),
}

# User-facing aliases resolved at registry lookup.
SEARCH_ENGINE_ALIASES: dict[str, str] = {}


def register(name: str, loader: Callable[[], type[SearchEngine]]) -> None:
    _REGISTRY[name] = loader


def register_alias(alias: str, canonical: str) -> None:
    """Register ``alias`` as a user-facing name for canonical engine ``canonical``."""
    SEARCH_ENGINE_ALIASES[alias] = canonical


def resolve_alias(name: str) -> str:
    """Return the canonical engine name for ``name``, or ``name`` if no alias."""
    return SEARCH_ENGINE_ALIASES.get(name, name)


def get_search_engine(name: str) -> type[SearchEngine]:
    """Return the search engine class for ``name`` (alias-resolved). Raises KeyError."""
    canonical = resolve_alias(name)
    try:
        loader = _REGISTRY[canonical]
    except KeyError as exc:
        raise KeyError(
            f"unknown search engine: {name!r} (resolved to {canonical!r}). "
            f"known: {sorted(_REGISTRY)}"
        ) from exc
    return loader()


def list_search_engine_names() -> list[str]:
    return sorted(_REGISTRY)


# "ddg" resolves to the keyless DuckDuckGo default.
register_alias("ddg", "duckduckgo")


__all__ = [
    "SEARCH_ENGINE_ALIASES",
    "get_search_engine",
    "list_search_engine_names",
    "register",
    "register_alias",
    "resolve_alias",
]
