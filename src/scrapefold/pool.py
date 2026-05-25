"""EnginePool — lazy, alias-resolving engine cache spanning the lifetime of a
single crawl_site() call (or a single standalone scrape).

The pool exists to defeat the trivial-client-reuse trap: if the router
constructs+closes engines per walk, then a 50-URL crawl pays 50 TLS
handshakes (HTTP-tier engines) and 50 SDK-init costs (Firecrawl/Apify/
Outscraper). With a pool whose lifetime spans the crawl, each engine is
constructed once and aclose()d once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

from scrapefold.engines import get_engine, resolve_alias
from scrapefold.engines.base import ScrapeEngine

logger = logging.getLogger(__name__)


class EnginePool:
    """Cache of constructed engine instances, keyed by canonical engine name."""

    def __init__(self) -> None:
        self._engines: dict[str, ScrapeEngine] = {}
        self._closed = False

    def get(self, name: str) -> ScrapeEngine:
        """Return the engine for *name*, constructing it on first request.

        Raises KeyError when the engine is not registered.
        Raises RuntimeError when called on an already-closed pool.
        """
        if self._closed:
            raise RuntimeError("EnginePool: pool is already closed")
        canonical = resolve_alias(name)
        if canonical not in self._engines:
            cls = get_engine(canonical)
            self._engines[canonical] = cls()
        return self._engines[canonical]

    async def aclose(self) -> None:
        """Close every constructed engine in parallel. Idempotent."""
        if self._closed:
            return
        self._closed = True
        names: list[str] = []
        tasks: list[Awaitable[Any]] = []
        for name, engine in self._engines.items():
            aclose = getattr(engine, "aclose", None)
            if callable(aclose):
                names.append(name)
                tasks.append(aclose())
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for name, outcome in zip(names, results, strict=True):
                if isinstance(outcome, BaseException):
                    logger.debug("pool: aclose engine=%s failed: %s", name, outcome)
        self._engines.clear()


__all__ = ["EnginePool"]
