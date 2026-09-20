"""Value types for the multi-engine search subsystem.

``SearchHit`` is one engine's single result (before fusion); ``SearchResult``
is a fused, deduped, RRF-scored result carrying an explainable per-engine score
breakdown. Both are frozen dataclasses, mirroring ``ScrapeResult`` in the
scrape side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchHit:
    """A single result from one search engine, before fusion.

    ``rank`` is the 0-based position in that engine's returned list and
    ``engine`` names the engine that produced it. Both are set by
    ``SearchEngine.search`` (the public wrapper), so ``_search`` implementations
    don't have to fill them.
    """

    url: str
    title: str = ""
    snippet: str = ""
    rank: int = 0
    engine: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    """A fused, deduped result across every engine that returned it.

    ``score`` is the total Reciprocal Rank Fusion score and ``score_breakdown``
    holds each engine's individual contribution — the explainability the whole
    subsystem exists to provide. ``rank`` is the 0-based final position after
    sorting.
    """

    url: str
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    rank: int = 0
    engines: tuple[str, ...] = ()
    score_breakdown: dict[str, float] = field(default_factory=dict)
    raw_by_engine: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def consensus(self) -> int:
        """How many engines returned this URL."""
        return len(self.engines)


__all__ = ["SearchHit", "SearchResult"]
