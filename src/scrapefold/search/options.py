"""Unified options for the search subsystem.

A single ``SearchOptions`` instance is passed to every search engine and to the
fusion step, mirroring how ``ScrapeOptions`` works on the scrape side. Engines
read only the fields they understand and ignore the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class SearchOptions:
    """Unified options passed to every search engine and to fusion."""

    count: int = 10
    """Maximum number of fused results to return."""

    engines: tuple[str, ...] | None = None
    """Explicit engine names to query. ``None`` = every available engine."""

    language: str | None = None
    """Two-letter ISO code, e.g. ``"ru"``. Maps to each engine's language key."""

    country: str | None = None
    """Two-letter ISO code, e.g. ``"us"``. Maps to each engine's region key."""

    timeout_s: int = 30
    rrf_k: int = 60
    """Reciprocal Rank Fusion constant (larger = flatter rank weighting)."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Engine-prefixed override keys, e.g. ``{"serper_tbs": "qdr:d"}``."""

    def with_updates(self, **changes: Any) -> SearchOptions:
        """Return a new ``SearchOptions`` with the given fields updated. Frozen-safe."""
        return replace(self, **changes)


__all__ = ["SearchOptions"]
