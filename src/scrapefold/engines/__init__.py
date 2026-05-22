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


# User-facing aliases for multi-mode engines so ``opts.engines=["scrapling"]``
# resolves to the canonical ``scrapling_stealth``, while
# ``WalkBudget.engines_tried`` stays keyed by unambiguous canonical names.
ENGINE_ALIASES: dict[str, str] = {}


def register(name: str, loader: Callable[[], type[ScrapeEngine]]) -> None:
    _REGISTRY[name] = loader


def register_alias(alias: str, canonical: str) -> None:
    """Register ``alias`` as a user-facing name for canonical engine ``canonical``."""
    ENGINE_ALIASES[alias] = canonical


def resolve_alias(name: str) -> str:
    """Return the canonical engine name for ``name``, or ``name`` if no alias."""
    return ENGINE_ALIASES.get(name, name)


def get_engine(name: str) -> type[ScrapeEngine]:
    """Return the engine class for ``name`` (alias-resolved). Raises KeyError if unknown."""
    canonical = resolve_alias(name)
    try:
        loader = _REGISTRY[canonical]
    except KeyError as exc:
        raise KeyError(
            f"unknown engine: {name!r} (resolved to {canonical!r}). known: {sorted(_REGISTRY)}"
        ) from exc
    return loader()


def list_engine_names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "ENGINE_ALIASES",
    "get_engine",
    "list_engine_names",
    "register",
    "register_alias",
    "resolve_alias",
]
