"""Base interface for every search engine.

Each engine subclasses ``SearchEngine``, declares its name, whether it needs an
API key, and an estimated per-call cost, then implements the async ``_search``
method. The public ``search`` wrapper handles timing, uniform error wrapping,
and stamping ``rank`` + ``engine`` onto each returned hit.

Adding a new search engine
--------------------------
1. Create ``src/scrapefold/search/engines/<name>.py`` with a class
   ``class XSearchEngine(SearchEngine):``.
2. Set ``NAME``, ``REQUIRES_API_KEY``, ``ESTIMATED_COST_USD``.
3. Implement ``async def _search(self, query, opts) -> list[SearchHit]``.
4. Register it in ``search/engines/__init__.py``.
5. Add tests under ``tests/test_search_<name>.py``.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import ClassVar

from scrapefold.search.options import SearchOptions
from scrapefold.search.types import SearchHit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchEngineError(Exception):
    """Raised when a search engine fails. Wraps the original exception."""

    engine: str
    message: str

    def __str__(self) -> str:
        return f"[{self.engine}] {self.message}"


class SearchEngine(ABC):
    """Abstract base for every search engine."""

    NAME: ClassVar[str] = ""
    REQUIRES_API_KEY: ClassVar[bool] = True
    ESTIMATED_COST_USD: ClassVar[float] = 0.0

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def search(self, query: str, opts: SearchOptions | None = None) -> list[SearchHit]:
        """Public entry. Handles timing, error wrapping, and rank/engine stamping."""
        opts = opts or SearchOptions()
        started = time.monotonic()
        try:
            hits = await self._search(query, opts)
        except SearchEngineError:
            raise
        except Exception as exc:
            logger.warning("%s search failed for %r: %s", self.NAME, query, exc)
            raise SearchEngineError(self.NAME, str(exc)) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.debug(
            "engine=%s query=%r hits=%d elapsed_ms=%d",
            self.NAME,
            query,
            len(hits),
            elapsed_ms,
        )
        return [replace(hit, rank=position, engine=self.NAME) for position, hit in enumerate(hits)]

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    @abstractmethod
    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        """Engine-specific search. ``rank`` / ``engine`` are stamped by the wrapper."""

    def is_available(self) -> bool:
        """Whether this engine can run right now (API key present when required)."""
        return not self.REQUIRES_API_KEY or bool(self.api_key)


__all__ = ["SearchEngine", "SearchEngineError"]
