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
from typing import ClassVar, Literal

from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

BillingUnit = Literal["call", "page", "minute", "gb"]
"""Pricing dimension shared by ``EngineCapabilities`` and ladder ``_StepBase``."""

ProxyType = Literal["none", "datacenter", "residential", "mobile"]
ProbeScope = Literal["none", "per_url", "per_domain", "per_session"]


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

    # --- Cost model ---
    estimated_cost_usd: float = 0.0
    """Per-call USD estimate (or per-unit cost when ``billing_unit != 'call'``)."""

    billing_unit: BillingUnit = "call"
    """Pricing dimension. ``router.estimate_step_cost`` reads this together
    with ``estimated_cost_usd`` and (for ``"gb"``) the engine's
    ``avg_response_mb_estimate``."""

    avg_response_mb_estimate: float = 2.0
    """Expected response size for ``billing_unit == "gb"``. Browser/unlocker
    engines should override (sessions easily push 10-50 MB)."""

    # --- Vendor / geography / policy ---
    requires_api_key: bool = True
    geography: tuple[str, ...] = ()
    """Country codes where this engine's proxies / endpoints are useful.
    Empty tuple = global / no preference."""

    proxy_type: ProxyType = "none"
    legal_constraints: tuple[str, ...] = ()
    """Tags the router cross-checks against ``Policy.legal_constraints_blocked``
    (e.g. ``"consent_required_linkedin"``, ``"no_paid_government"``)."""

    default_timeout_s: int = 60

    # --- Classification ---
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

    PROBE_SCOPE: ClassVar[ProbeScope] = "none"
    """How often ``probe()`` must succeed before the engine is considered usable.

    - ``none``: no probe (default).
    - ``per_url``: probe each URL — only when viability genuinely varies per URL.
    - ``per_domain``: probe once per registered domain (e.g. ``reddit.com``)
      for the session. Subdomains share the cache key.
    - ``per_session``: probe once per process lifetime.

    The router (S7) maintains the cache; engines just declare scope and
    implement ``probe()``.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    async def probe(self, url: str) -> bool:
        """Cheap viability check. Default: always usable.

        Engines whose backend API can disappear (Reddit's ``/.json``, vendor
        deprecations) override this with a HEAD or low-cost call. The router
        caches the result per ``PROBE_SCOPE``.
        """
        return True

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
        except EngineError as exc:
            # Re-raise structured errors from _fetch without double-wrapping.
            # Patch elapsed_ms if the engine left it at 0.
            elapsed = int((time.monotonic() - started) * 1000)
            logger.warning("%s failed for %s: %s", self.NAME, url, exc)
            if exc.elapsed_ms == 0:
                raise replace(exc, elapsed_ms=elapsed) from exc.__cause__
            raise
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            logger.warning("%s failed for %s: %s", self.NAME, url, exc)
            raise EngineError(self.NAME, str(exc), elapsed) from exc
        elapsed = int((time.monotonic() - started) * 1000)
        # Patch elapsed_ms in case the engine didn't set it
        if result.elapsed_ms == 0:
            result = replace(result, elapsed_ms=elapsed)
        # Optional main-content extraction is engine-agnostic: it runs on the
        # HTML any engine returned, so it lives here rather than in each _fetch.
        # Keyed off the ORIGINAL opts because main_content is not in any
        # engine's SUPPORTED_OPTIONS and would be stripped before _fetch.
        if opts.main_content and result.html:
            result = self._apply_main_content(result)
        return result

    @staticmethod
    def _apply_main_content(result: ScrapeResult) -> ScrapeResult:
        """Re-derive text/markdown from the main article body when possible.

        No-op (returns ``result`` unchanged) when trafilatura is unavailable or
        finds no extractable content — so enabling ``main_content`` can never
        blank an otherwise-good result.
        """
        from scrapefold.html_to_text import html_to_main_content

        extracted = html_to_main_content(result.html or "", base_url=result.url)
        if extracted is None:
            return result
        text, markdown = extracted
        return replace(result, text=text, markdown=markdown)

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


@dataclass(frozen=True)
class RedirectScopeViolation(EngineError):  # noqa: N818 — intentional domain-specific name
    """Raised when a redirect leaves the declared same-host scope.

    Subclass of :class:`EngineError` so all existing ``isinstance(exc,
    EngineError)`` checks still match.  The router catches this specific
    subclass *before* the generic ``EngineError`` handler and terminates the
    walk immediately — no escalation to other engines.

    ``target`` is the off-host URL that triggered the violation.
    """

    target: str = ""


__all__ = [
    "BillingUnit",
    "EngineCapabilities",
    "EngineError",
    "ProbeScope",
    "ProxyType",
    "RedirectScopeViolation",
    "ScrapeEngine",
]
