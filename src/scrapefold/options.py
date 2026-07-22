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
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping


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

    # --- Proxy (unified single exit + rotation pool) ---
    proxy: str | None = None
    """A single exit proxy URL, e.g. ``"http://user:pass@host:8000"``.

    Unified across every proxy-capable engine: each maps it to its native proxy
    option (Camoufox ``proxy`` dict, pydoll ``--proxy-server``, scrapling
    ``proxy``). Vendor engines that manage their own proxy fleet
    (``premium_proxy`` / ``country``) don't list it in ``SUPPORTED_OPTIONS``, so
    it's dropped for them. Usually set by the rotation layer, not by hand — see
    ``proxies`` below.
    """

    proxies: tuple[str, ...] | None = None
    """A rotation pool of exit proxies ("proxy over proxy").

    When set, the router builds a health-scored
    :class:`~scrapefold.proxy.SessionPool` and, for each proxy-capable engine,
    threads one exit into ``proxy`` per attempt — retrying the *same* engine
    behind a *different* exit IP when a response looks blocked, before
    escalating to the next (more expensive) tier. Retirement / rotation policy
    is owned by the pool; the engine still sees just one ``proxy``. Pass a
    pre-built, crawl-spanning pool via ``extra["proxy_pool"]`` instead for
    advanced control.
    """

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

    main_content: bool = False
    """Extract only the main article body, dropping nav / ads / boilerplate.

    When ``True`` and the engine returned HTML, ``ScrapeResult.text`` and
    ``.markdown`` are re-derived from the main content via Trafilatura (needs
    the ``trafilatura`` extra). Applied centrally in ``ScrapeEngine.scrape`` for
    every HTML-producing engine, so any engine in a fallback chain honors it.
    Degrades gracefully: if trafilatura is not installed or finds no article,
    the engine's full-page text/markdown is kept unchanged.
    """

    # --- Crawl scope (used by crawl_site, ignored by single scrape) ---
    max_pages: int = 50
    max_depth: int = 3
    follow_subdomains: bool = False

    autothrottle: bool = False
    """Adapt the per-host crawl delay to observed latency (Scrapy-style).

    When ``True``, :func:`~scrapefold.crawler.crawl` sleeps an adaptive delay
    before each page fetch and folds the response latency + status into a
    per-host controller (:class:`~scrapefold.crawler.throttle.AutoThrottle`):
    the delay eases toward ``latency / target_concurrency``, never shrinks on an
    error, and backs off hard on ``429`` / ``503``. Keeps a large crawl polite
    on slow or rate-limiting origins instead of hammering at a fixed rate.
    Tuning knobs live in ``extra["autothrottle_*"]`` (``target_concurrency``,
    ``start_delay``, ``max_delay``, ``min_delay``). Ignored by single scrape.
    """

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
    """Engine-prefixed override keys, e.g. ``{"firecrawl_replaceAllPathsWithAbsolutePaths": True}``.

    Also the home for walk-level policy: set ``extra["policy"]`` to a
    ``scrapefold.ladders.Policy`` instance to override the per-class
    default in ``DEFAULT_POLICY``.
    """

    def with_updates(self, **changes: Any) -> ScrapeOptions:
        """Return a new ScrapeOptions with the given fields updated. Frozen-safe."""
        from dataclasses import replace

        return replace(self, **changes)


def cookies_to_header(cookies: Mapping[str, str] | None) -> str | None:
    """Serialize a cookies dict to a single ``Cookie`` header value, or ``None``."""
    if not cookies:
        return None
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def cookies_to_playwright_list(cookies: Mapping[str, str] | None, url: str) -> list[dict[str, str]]:
    """Convert a cookies dict to the Playwright cookie-object list, scoped to ``url``.

    Playwright's ``context.add_cookies`` (used by scrapling_stealth and crawl4ai's
    BrowserConfig) rejects raw dicts and silently drops cookies whose ``url`` does
    not match the navigation target's scheme + host. Both engines now route cookies
    through this helper so a placeholder URL can never make auth scrapes a no-op.
    """
    if not cookies:
        return []
    return [{"name": k, "value": v, "url": url} for k, v in cookies.items()]


def build_target_headers(
    opts: ScrapeOptions,
    *,
    include_cookies: bool = True,
    include_user_agent: bool = True,
) -> dict[str, str]:
    """Project shared header fields from ``ScrapeOptions`` to a dict.

    Engines layer vendor-specific keys on top of the result. Caller-provided
    ``opts.custom_headers`` always win (applied last).

    ``include_user_agent=False`` is for engines whose SDK has a dedicated
    user-agent kwarg (scrapling, crawl4ai, cloakbrowser, selenium) — putting
    UA in the headers dict would conflict with the vendor's own UA handling.
    """
    headers: dict[str, str] = {}
    if opts.language:
        headers["Accept-Language"] = opts.language
    if include_user_agent and opts.user_agent:
        headers["User-Agent"] = opts.user_agent
    if include_cookies:
        cookie_value = cookies_to_header(opts.cookies)
        if cookie_value:
            headers["Cookie"] = cookie_value
    if opts.custom_headers:
        headers.update(opts.custom_headers)
    return headers


def strip_extra_prefix(extra: Mapping[str, Any] | None, prefix: str) -> dict[str, Any]:
    """Return ``{k[len(prefix):]: v}`` for every key in ``extra`` starting with ``prefix``."""
    if not extra:
        return {}
    plen = len(prefix)
    return {k[plen:]: v for k, v in extra.items() if k.startswith(prefix)}


__all__ = [
    "ScrapeOptions",
    "build_target_headers",
    "cookies_to_header",
    "cookies_to_playwright_list",
    "strip_extra_prefix",
]
