"""Base interface for every scrapefold engine.

Each engine subclasses ``ScrapeEngine``, declares its name, capabilities,
and the subset of ``ScrapeOptions`` it actually supports, then implements
the async ``_fetch`` method. The public ``scrape`` entrypoint handles
option-stripping, timing, cost tracking, and uniform error wrapping.

Adding a new engine
-------------------
1. Create ``src/scrapefold/engines/<name>.py`` with a class
   ``class XEngine(ScrapeEngine):``.
2. Set ``NAME``, ``CAPABILITIES``, ``SUPPORTED_OPTIONS``.
3. Implement ``async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult``.
4. Register it in ``engines/__init__.py``.
5. Add tests under ``tests/test_engines/test_<name>.py``.

See ``docs/conventions/golden-rules.md`` and ``CONTRIBUTING.md`` for the
full checklist.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import ClassVar

from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EngineCapabilities:
    """Static declaration of what an engine can do.

    Surfaced via the ``list-engines`` CLI and the ``list_engines`` MCP tool
    so the router can pick smartly and humans can compare options at a glance.
    """

    js_rendering: bool = False
    stealth: bool = False
    screenshot: bool = False
    crawl_native: bool = False
    """Has a native crawl endpoint (e.g. Firecrawl ``/crawl``)."""

    cost_per_1k: float = 0.0
    """Approximate USD per 1000 pages. ``0`` for free / local."""

    requires_api_key: bool = True
    site_classified: bool = False
    """Has site-specific endpoints (LinkedIn, Amazon, Instagram, …)."""

    output_native_markdown: bool = False
    free_tier: bool = True
    deprecated: bool = False
    """Marked for removal; not in the auto-selection chain."""


class ScrapeEngine(ABC):
    """Abstract base for every scrape engine."""

    NAME: ClassVar[str] = ""
    CAPABILITIES: ClassVar[EngineCapabilities] = EngineCapabilities()
    SUPPORTED_OPTIONS: ClassVar[frozenset[str]] = frozenset()
    """Names of ``ScrapeOptions`` fields the engine honors. Other fields
    are stripped (with a DEBUG log) before ``_fetch`` is called."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def scrape(self, url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        """Public entry. Handles option-stripping + timing + error wrapping."""
        opts = opts or ScrapeOptions()
        stripped = self._strip_unsupported(opts)
        started = time.monotonic()
        try:
            result = await self._fetch(url, stripped)
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            logger.warning("%s failed for %s: %s", self.NAME, url, exc)
            raise EngineError(self.NAME, str(exc), elapsed) from exc
        elapsed = int((time.monotonic() - started) * 1000)
        # Patch elapsed_ms in case the engine didn't set it
        if result.elapsed_ms == 0:
            result = replace(result, elapsed_ms=elapsed)
        return result

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    @abstractmethod
    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Engine-specific fetch. Receives only supported options."""

    def is_available(self) -> bool:
        """Whether this engine can run right now (vendor SDK installed, key present)."""
        return not self.CAPABILITIES.requires_api_key or bool(self.api_key)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _strip_unsupported(self, opts: ScrapeOptions) -> ScrapeOptions:
        """Return a copy of ``opts`` with unsupported fields reset to defaults.

        Unsupported fields are logged at DEBUG level so behavior is debuggable
        without spamming production logs.
        """
        if not self.SUPPORTED_OPTIONS:
            return opts
        defaults = ScrapeOptions()
        changes: dict[str, object] = {}
        for fname in opts.__dataclass_fields__:
            if fname in self.SUPPORTED_OPTIONS:
                continue
            current = getattr(opts, fname)
            default = getattr(defaults, fname)
            if current != default:
                logger.debug(
                    "engine=%s dropping unsupported opt=%s value=%r",
                    self.NAME,
                    fname,
                    current,
                )
                changes[fname] = default
        return opts.with_updates(**changes) if changes else opts


@dataclass(frozen=True)
class EngineError(Exception):
    """Raised when an engine fails. Wraps the original exception."""

    engine: str
    message: str
    elapsed_ms: int = 0
    cause: BaseException | None = field(default=None, compare=False, repr=False)

    def __str__(self) -> str:
        return f"[{self.engine}] {self.message}"


__all__ = ["EngineCapabilities", "EngineError", "ScrapeEngine"]
