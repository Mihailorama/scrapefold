"""Engine registry.

Engines are imported lazily so that an optional vendor SDK missing on the
machine (e.g. ``selenium`` not installed) does not break import of
``scrapefold`` itself. ``get_engine(name)`` returns a class on demand.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapefold.engines.base import ScrapeEngine

# Lazy registry: name -> import-and-return-class function.
# Each lambda imports the engine module on first call so missing extras
# only error out when that engine is actually requested.
_REGISTRY: dict[str, Callable[[], type[ScrapeEngine]]] = {}


def register(name: str, loader: Callable[[], type[ScrapeEngine]]) -> None:
    _REGISTRY[name] = loader


def get_engine(name: str) -> type[ScrapeEngine]:
    """Return the engine class for ``name``. Raises KeyError if unknown."""
    try:
        loader = _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown engine: {name!r}. known: {sorted(_REGISTRY)}") from exc
    return loader()


def list_engine_names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["get_engine", "list_engine_names", "register"]
