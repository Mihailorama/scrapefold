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


# Backward-compat aliases for multi-mode engines.
#
# Some engines have multiple operating modes that need distinct registered
# names so ``WalkBudget.engines_tried`` dedup is unambiguous (Codex round-2
# review, finding R2-NEW-2). But users still want to write
# ``opts.engines=["scrapling"]`` and get the sensible default mode.
#
# ``resolve_alias("scrapling")`` returns ``"scrapling_stealth"``.
# ``resolve_alias("scrapling_stealth")`` is a no-op.
#
# Engines populating these aliases lands alongside the engine ports in
# S2-S11. See ``docs/TECH_DEBT.md`` P1 item #6.
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
