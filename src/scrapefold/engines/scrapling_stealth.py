"""Scrapling stealth engine for scrapefold.

Uses ``StealthyFetcher`` (Playwright + stealth patches) for JS-rendered pages
with bot-detection evasion. Free / local — no API key required.

SDK: scrapling (install with ``pip install scrapefold[scrapling]`` or
     ``pip install 'scrapling[fetchers]>=0.4'``).

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require scrapling to be installed.

Introspected signature (scrapling 0.4.x):
  StealthyFetcher.fetch(url: str, **kwargs: Unpack[StealthSession]) -> Response
  StealthSession key fields: useragent, extra_headers, cookies, timeout,
      wait, wait_selector, proxy
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import (
    ScrapeOptions,
    build_target_headers,
    cookies_to_playwright_list,
    strip_extra_prefix,
)
from scrapefold.result import ScrapeResult

_DEFAULT_OPTS = ScrapeOptions()

logger = logging.getLogger(__name__)

# Module-level reference populated lazily on first use.
# Tests can monkeypatch: patch("scrapefold.engines.scrapling_stealth.StealthyFetcher", mock_cls)
StealthyFetcher: Any = None


def _load_stealth_fetcher() -> Any:
    """Return the StealthyFetcher class, importing scrapling on first call.

    Raises ImportError with an installation hint if scrapling is missing.
    """
    global StealthyFetcher
    if StealthyFetcher is not None:
        return StealthyFetcher
    try:
        from scrapling.fetchers import StealthyFetcher as _StealthyFetcher  # lazy
    except ImportError as exc:
        raise ImportError(
            "scrapling is required for ScraplingStealthEngine. "
            "Install it with: pip install 'scrapling[fetchers]>=0.4'"
        ) from exc
    StealthyFetcher = _StealthyFetcher
    return StealthyFetcher


def _adapt(opts: ScrapeOptions, url: str) -> dict[str, Any]:
    """Map unified ScrapeOptions to StealthyFetcher.fetch kwargs.

    Supported StealthSession kwargs used:
      useragent    — from opts.user_agent
      extra_headers — from opts.custom_headers + Accept-Language
      cookies       — Playwright cookie list scoped to ``url``
      timeout       — from opts.timeout_s (converted to ms)
      wait          — from opts.wait_ms
      wait_selector — from opts.wait_for_selector
    """
    kwargs: dict[str, Any] = {}

    # user_agent and cookies go to dedicated SDK kwargs, not into the
    # headers dict — opt out of include_user_agent/include_cookies here.
    headers = build_target_headers(opts, include_cookies=False, include_user_agent=False)
    if headers:
        kwargs["extra_headers"] = headers

    if opts.user_agent:
        kwargs["useragent"] = opts.user_agent

    if opts.cookies:
        kwargs["cookies"] = cookies_to_playwright_list(opts.cookies, url)

    if opts.wait_ms != _DEFAULT_OPTS.wait_ms:
        kwargs["wait"] = opts.wait_ms

    if opts.wait_for_selector:
        kwargs["wait_selector"] = opts.wait_for_selector

    # Always forward the scrapefold timeout so scrapling's internal 30s
    # default cannot win silently — even when timeout_s matches our default.
    kwargs["timeout"] = opts.timeout_s * 1000  # StealthSession expects ms

    # Unified proxy → StealthSession's `proxy` (the exit the rotation pool
    # threads in). An explicit scrapling_proxy in extra still overrides it.
    if opts.proxy:
        kwargs["proxy"] = opts.proxy

    kwargs.update(strip_extra_prefix(opts.extra, "scrapling_stealth_"))
    kwargs.update(strip_extra_prefix(opts.extra, "scrapling_"))
    return kwargs


class ScraplingStealthEngine(ScrapeEngine):
    """Scrapling stealth (Playwright-based) engine.

    Uses ``StealthyFetcher`` which runs a full Chromium browser with stealth
    patches to evade bot detection. Free to use — no API key required.

    The SDK (scrapling) is lazy-imported so that importing scrapefold doesn't
    require it to be installed.
    """

    NAME = "scrapling_stealth"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="datacenter",
        output_native_markdown=False,
        default_timeout_s=60,
        avg_response_mb_estimate=15.0,  # full browser session
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "render_js",
            "wait_ms",
            "wait_for_selector",
            "user_agent",
            "custom_headers",
            "cookies",
            "output_format",
            "timeout_s",
            "proxy",
            "extra",
        }
    )

    def is_available(self) -> bool:
        """Always available — scrapling requires no API key."""
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Call StealthyFetcher.fetch (sync) in a thread, map response to ScrapeResult."""
        fetcher_cls = _load_stealth_fetcher()
        kwargs = _adapt(opts, url)

        logger.debug("scrapling_stealth fetch url=%s kwargs=%s", url, list(kwargs))

        # StealthyFetcher.fetch is synchronous — run in thread to stay async
        response = await asyncio.to_thread(fetcher_cls.fetch, url, **kwargs)

        html = str(response.html_content)
        text, markdown = html_to_both(html, base_url=url)

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=0.0,
            meta={"status_code": getattr(response, "status", None)},
        )


__all__ = ["ScraplingStealthEngine", "_adapt"]
