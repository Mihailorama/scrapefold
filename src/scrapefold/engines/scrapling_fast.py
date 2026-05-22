"""Scrapling fast engine for scrapefold.

Uses ``AsyncFetcher`` (HTTP-only, curl_cffi-based, no browser) for lightweight
fast scraping. Free / local — no API key required.

SDK: scrapling (install with ``pip install scrapefold[scrapling]`` or
     ``pip install 'scrapling[fetchers]>=0.4'``).

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require scrapling to be installed.

Introspected signature (scrapling 0.4.x):
  AsyncFetcher.get(url: str, **kwargs: Unpack[GetRequestParams]) -> Awaitable[Response]
  GetRequestParams key fields: headers, cookies, timeout, proxies, proxy
"""

from __future__ import annotations

import logging
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

_DEFAULT_OPTS = ScrapeOptions()

logger = logging.getLogger(__name__)

# Module-level reference populated lazily on first use.
# Tests can monkeypatch: patch("scrapefold.engines.scrapling_fast.AsyncFetcher", mock_cls)
AsyncFetcher: Any = None


def _load_async_fetcher() -> Any:
    """Return the AsyncFetcher class, importing scrapling on first call.

    Raises ImportError with an installation hint if scrapling is missing.
    """
    global AsyncFetcher
    if AsyncFetcher is not None:
        return AsyncFetcher
    try:
        from scrapling.fetchers import AsyncFetcher as _AsyncFetcher  # lazy
    except ImportError as exc:
        raise ImportError(
            "scrapling is required for ScraplingFastEngine. "
            "Install it with: pip install 'scrapling[fetchers]>=0.4'"
        ) from exc
    AsyncFetcher = _AsyncFetcher
    return AsyncFetcher


def _adapt(opts: ScrapeOptions) -> dict[str, Any]:
    """Map unified ScrapeOptions to AsyncFetcher.get kwargs.

    Supported GetRequestParams kwargs used:
      headers  — from opts.user_agent + opts.language + opts.custom_headers
      cookies  — from opts.cookies (passed as dict)
      timeout  — from opts.timeout_s (in seconds, float/int)
    """
    kwargs: dict[str, Any] = {}

    headers = build_target_headers(opts, include_cookies=False)
    if headers:
        kwargs["headers"] = headers

    if opts.cookies:
        kwargs["cookies"] = opts.cookies

    if opts.timeout_s != _DEFAULT_OPTS.timeout_s:
        kwargs["timeout"] = opts.timeout_s

    kwargs.update(strip_extra_prefix(opts.extra, "scrapling_fast_"))
    kwargs.update(strip_extra_prefix(opts.extra, "scrapling_"))
    return kwargs


class ScraplingFastEngine(ScrapeEngine):
    """Scrapling fast (HTTP-only) engine.

    Uses ``AsyncFetcher`` which makes curl_cffi-based HTTP requests with
    optional browser impersonation headers — very fast, no browser overhead.
    Free to use — no API key required.

    The SDK (scrapling) is lazy-imported so that importing scrapefold doesn't
    require it to be installed.
    """

    NAME = "scrapling_fast"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=False,
        screenshot=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="datacenter",
        output_native_markdown=False,
        default_timeout_s=10,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "user_agent",
            "custom_headers",
            "cookies",
            "output_format",
            "timeout_s",
            "extra",
        }
    )

    def is_available(self) -> bool:
        """Always available — scrapling requires no API key."""
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Call AsyncFetcher.get (awaitable), map response to ScrapeResult."""
        fetcher_cls = _load_async_fetcher()
        kwargs = _adapt(opts)

        logger.debug("scrapling_fast fetch url=%s kwargs=%s", url, list(kwargs))

        # AsyncFetcher.get returns an Awaitable[Response]
        response = await fetcher_cls.get(url, **kwargs)

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


__all__ = ["ScraplingFastEngine", "_adapt"]
