"""Unified parameter schema for every scrape engine.

A single ``ScrapeOptions`` instance is passed into every engine. Engines
declare a ``SUPPORTED_OPTIONS`` set on their class; the base ``ScrapeEngine``
strips unsupported keys with a DEBUG log line before delegating to the
engine-specific call. Engines never raise on an unknown option.

Per-engine adapters (each engine module's ``_adapt(opts)`` function) translate
unified values into the native vendor parameter shape — e.g. ``country="ru"``
becomes Scrapingdog's TLD-routed proxy, ScrapingBee's ``country_code``, or
is dropped for Jina.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ScrapeOptions:
    """Unified options passed to every scrape engine."""

    # --- Localization ---
    language: str | None = None
    """Two-letter ISO code, e.g. ``"ru"``. Maps to Accept-Language header or vendor-specific key."""

    country: str | None = None
    """Two-letter ISO code for proxy region, e.g. ``"ru"``."""

    timezone: str | None = None

    # --- JS rendering ---
    render_js: bool = True
    wait_ms: int = 5000
    wait_for_selector: str | None = None
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded"

    # --- Anti-bot / stealth ---
    stealth: bool = False
    premium_proxy: bool = False
    user_agent: str | None = None
    custom_headers: dict[str, str] | None = None
    cookies: dict[str, str] | None = None

    # --- Output shape ---
    output_format: Literal["text", "markdown", "html", "json", "auto"] = "auto"
    """Preferred return format.

    - ``text`` — plain text only (engines may still fill markdown/html as
      a side-effect; this is a hint, not a guarantee).
    - ``markdown`` — markdown only (default for site→big-markdown crawls).
    - ``html`` — raw HTML; ``ScrapeResult.html`` will be populated.
    - ``json`` — only meaningful for engines that return structured data
      natively (Firecrawl ``/extract``, AnySite, Apify). Requires also
      passing an ``extra["schema"]`` for structured-extract engines.
    - ``auto`` — let the engine pick its native cheapest output.
    """

    take_screenshot: bool = False
    include_links: bool = True
    include_external_links: bool = False

    # --- Crawl scope (used by crawl_site, ignored by single scrape) ---
    max_pages: int = 50
    max_depth: int = 3
    follow_subdomains: bool = False

    # --- Engine selection / orchestration ---
    engines: tuple[str, ...] | None = None
    """Ordered fallback chain. ``None`` lets the router auto-select."""

    parallel: bool = False
    """Run every engine in ``engines`` concurrently and LLM-merge results."""

    timeout_s: int = 60

    # --- Cache ---
    skip_cache: bool = False

    # --- Escape hatch ---
    extra: dict[str, Any] = field(default_factory=dict)
    """Engine-prefixed override keys, e.g. ``{"firecrawl_replaceAllPathsWithAbsolutePaths": True}``."""

    def with_updates(self, **changes: Any) -> ScrapeOptions:
        """Return a new ScrapeOptions with the given fields updated. Frozen-safe."""
        from dataclasses import replace

        return replace(self, **changes)


__all__ = ["ScrapeOptions"]
