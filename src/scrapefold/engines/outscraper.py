"""Outscraper engine for scrapefold.

Outscraper SaaS — paid, JS-rendering, site-classified (LinkedIn / company data).
Returns structured JSON data natively via the company_insights endpoint.

Cost: ~$0.003 per call.
SDK: outscraper (install with ``pip install scrapefold[outscraper]``).

SDK contract (introspected 2026-05-22, outscraper 5.x):
  - ApiClient.company_insights(query, fields=None, async_request=False, enrichment=None)
      -> list[dict] | dict
  - No dedicated linkedin_profiles method exists in the SDK.
  - All URLs (company pages, profile pages, domain names) are passed to company_insights.
  - Return shape: list[dict] for a single synchronous query — each dict is one URL's data.
  - The SDK is sync-only (uses requests internally); calls are wrapped in asyncio.to_thread.

Routing decision:
  All URLs are forwarded to company_insights regardless of URL pattern.
  The Outscraper API handles both company URLs (linkedin.com/company/X) and
  individual profile URLs (linkedin.com/in/X) through the same endpoint.
  No profile-specific routing is implemented because the SDK has no separate method.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

# Module-level reference to the SDK class, populated lazily on first use.
# Exposed at module scope so tests can monkeypatch it:
#   patch("scrapefold.engines.outscraper.ApiClient", ...)
ApiClient: Any = None


def _load_sdk() -> Any:
    """Return the ApiClient class, importing it on first call.

    Raises ImportError with an installation hint if outscraper is missing.
    """
    global ApiClient
    if ApiClient is not None:
        return ApiClient
    try:
        import outscraper as _os  # lazy
    except ImportError as exc:
        raise ImportError(
            "outscraper is required for OutscraperEngine. "
            "Install it with: pip install scrapefold[outscraper]"
        ) from exc
    ApiClient = _os.ApiClient
    return ApiClient


class OutscraperEngine(ScrapeEngine):
    """Outscraper-backed scrape engine.

    Uses the ``company_insights`` endpoint for all URLs (company pages,
    LinkedIn profile pages, domain names). The Outscraper SDK has no
    dedicated ``linkedin_profiles`` method; ``company_insights`` handles
    both company and individual profile URLs.

    API key is read from the constructor argument or the ``OUTSCRAPER_API_KEY``
    environment variable. ``is_available()`` returns ``False`` when neither is set.

    Install the optional extra: ``pip install scrapefold[outscraper]``.

    Returns ``ScrapeResult`` with:
    - ``json``: the first item from the SDK response list (raw structured dict).
    - ``text`` and ``markdown``: pretty-printed JSON representation of the same data.
    - ``html``: always ``None`` (Outscraper returns structured data, not HTML).
    - ``cost_usd``: 0.003 per call.
    """

    NAME = "outscraper"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=False,
        estimated_cost_usd=0.003,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        output_native_markdown=False,
        site_classified=True,
        default_timeout_s=60,
    )
    # Outscraper handles LinkedIn URLs as opaque inputs; most browser
    # knobs (render_js, stealth, country, wait_ms, …) do not apply.
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("OUTSCRAPER_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* via the Outscraper company_insights endpoint.

        The SDK is sync-only (uses ``requests`` internally), so the call is
        wrapped in ``asyncio.to_thread`` to avoid blocking the event loop.

        SDK return shape: list[dict] — each dict contains structured data for
        one URL. We always take the first item.

        Raises ValueError (wrapped by base class into EngineError) when the SDK
        returns an empty list.
        """
        # Lazy-import; only fails when outscraper extra is not installed.
        client_cls = _load_sdk()
        client = client_cls(api_key=self.api_key)

        # Map opts.extra["outscraper_*"] keys to real SDK kwargs.
        # Supported: fields, async_request, enrichment (anything in company_insights signature).
        extra_kwargs = strip_extra_prefix(opts.extra, "outscraper_")

        logger.debug("outscraper company_insights url=%s extra_kwargs=%s", url, extra_kwargs)

        # SDK is synchronous — run in a thread pool to keep the event loop free.
        raw: list[dict[str, Any]] | dict[str, Any] = await asyncio.to_thread(
            client.company_insights,
            [url],
            **extra_kwargs,
        )

        # Normalise: company_insights returns list[dict] for sync single-URL calls.
        items: list[dict[str, Any]] = [raw] if isinstance(raw, dict) else raw

        if not items:
            raise ValueError(f"outscraper returned empty result for URL: {url}")

        data: dict[str, Any] = items[0]
        text_out, markdown_out = json_to_scrape_text(data)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=data,
        )


__all__ = ["OutscraperEngine"]
