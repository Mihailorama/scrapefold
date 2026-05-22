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
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.scrapingdog.com/scrape"


def _adapt(opts: ScrapeOptions, api_key: str, url: str) -> dict:
    """Map unified ScrapeOptions to Scrapingdog query params.

    Boolean parameters are serialized as ``"true"``/``"false"`` strings —
    Scrapingdog rejects Python's ``True`` literal.
    """
    params: dict[str, str] = {
        "api_key": api_key,
        "url": url,
        "dynamic": "true" if opts.render_js else "false",
    }

    if opts.country:
        params["country"] = opts.country

    params["wait"] = str(opts.wait_ms)

    if opts.premium_proxy:
        params["premium"] = "true"

    # Forward extra keys with "scrapingdog_" prefix, stripping the prefix.
    for key, value in (opts.extra or {}).items():
        if key.startswith("scrapingdog_"):
            param_name = key[len("scrapingdog_") :]
            params[param_name] = str(value)

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

        # Build request headers from options.
        # Order of precedence (lowest → highest): derived headers, custom_headers.
        headers: dict[str, str] = {}

        if opts.language:
            headers["Accept-Language"] = opts.language

        if opts.user_agent:
            headers["User-Agent"] = opts.user_agent

        # Cookies → Cookie header
        if opts.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in opts.cookies.items())
            headers["Cookie"] = cookie_str

        # custom_headers override everything derived above
        if opts.custom_headers:
            headers.update(opts.custom_headers)

        timeout = float(opts.timeout_s)

        async with httpx.AsyncClient(timeout=timeout) as client:
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
