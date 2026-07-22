"""ScrapingdogEngine — paid REST scraping via api.scrapingdog.com/scrape.

Pure REST, no SDK. Supports JS rendering, country routing, premium proxies.
Estimated cost: $0.0005 per call (lowest in the paid tier).

Specialized Scrapingdog endpoints (LinkedIn, Amazon, Twitter) are NOT handled
here — they belong in separate engines registered independently.
"""

from __future__ import annotations

import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.scrapingdog.com/scrape"


def _adapt(opts: ScrapeOptions, api_key: str, url: str) -> dict[str, str]:
    """Map unified ScrapeOptions to Scrapingdog query params.

    Boolean parameters are serialized as ``"true"``/``"false"`` strings —
    Scrapingdog rejects Python's ``True`` literal.
    """
    params: dict[str, str] = {
        "api_key": api_key,
        "url": url,
        "dynamic": "true" if opts.render_js else "false",
        "wait": str(opts.wait_ms),
    }
    if opts.country:
        params["country"] = opts.country
    if opts.premium_proxy:
        params["premium"] = "true"
    for key, value in strip_extra_prefix(opts.extra, "scrapingdog_").items():
        params[key] = str(value)
    return params


class ScrapingdogEngine(ScrapeEngine):
    """Scraping engine backed by the Scrapingdog REST API.

    API key is read from the constructor argument or the ``SCRAPINGDOG_API_KEY``
    environment variable. ``is_available()`` returns ``False`` when neither is set.
    """

    NAME = "scrapingdog"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=False,
        estimated_cost_usd=0.0005,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        default_timeout_s=60,
        avg_response_mb_estimate=3.0,  # rendered-HTML proxy API response
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "wait_ms",
            "premium_proxy",
            "user_agent",
            "custom_headers",
            "cookies",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPINGDOG_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* via Scrapingdog and return a ``ScrapeResult``."""
        params = _adapt(opts, self.api_key or "", url)
        headers = build_target_headers(opts)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(_ENDPOINT, params=params, headers=headers)

        raw_html = response.text
        text, markdown = html_to_both(raw_html, base_url=url)

        meta: dict[str, object] = {
            "status_code": response.status_code,
        }
        request_id = response.headers.get("x-request-id")
        if request_id is not None:
            meta["scrapingdog_request_id"] = request_id

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=raw_html,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=0.0005,
            meta=meta,
        )


__all__ = ["ScrapingdogEngine"]
