"""ScraperApiEngine — paid REST scraping via api.scraperapi.com.

Pure REST, no SDK. Supports JS rendering, country routing, premium proxies,
native markdown output, and AI-Parser structured extraction (``json`` slot).

Cost model: 1 credit per base request (~$0.00049 on the entry plan). JS
``render`` costs ~10 credits and ultra-premium more; ``estimated_cost_usd``
reflects the base credit and is a floor, not a ceiling.

AI Parser:
- ``extra["scraperapi_autoparse"] = "true"`` enables ScraperAPI's built-in
  domain parsers (Amazon, Google, …) → JSON response → ``json`` slot.
- ``extra["scraperapi_output_format"] = "json"`` (or a custom-parser param via
  the same ``scraperapi_`` passthrough) routes a custom AI Parser. Creating /
  editing parsers (30k credits each) is out of scope — see backlog.
"""

from __future__ import annotations

import json as _json
import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.scraperapi.com/"
_COST_USD = 0.00049


def _adapt(opts: ScrapeOptions, api_key: str, url: str) -> dict[str, str]:
    """Map unified ScrapeOptions to ScraperAPI query params.

    Booleans serialize as ``"true"`` / ``"false"`` strings. ``language``,
    ``user_agent``, ``cookies`` and ``custom_headers`` are request *headers*,
    handled by ``build_target_headers`` — not query params.
    """
    params: dict[str, str] = {
        "api_key": api_key,
        "url": url,
        "render": "true" if opts.render_js else "false",
    }
    if opts.country:
        params["country_code"] = opts.country
    if opts.premium_proxy:
        params["premium"] = "true"
    if opts.wait_for_selector:
        params["wait_for_selector"] = opts.wait_for_selector
    if opts.output_format == "markdown":
        params["output_format"] = "markdown"
    for key, value in strip_extra_prefix(opts.extra, "scraperapi_").items():
        params[key] = str(value)
    return params


def _is_json_response(response: httpx.Response, params: dict[str, str]) -> bool:
    """True when ScraperAPI returned structured JSON (AI Parser / autoparse)."""
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        return True
    return params.get("autoparse") == "true" or params.get("output_format") == "json"


class ScraperApiEngine(ScrapeEngine):
    """Scraping engine backed by the ScraperAPI REST API.

    API key from the constructor argument or the ``SCRAPERAPI_API_KEY``
    environment variable. ``is_available()`` returns ``False`` when neither
    is set.
    """

    NAME = "scraperapi"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=False,
        estimated_cost_usd=_COST_USD,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        output_native_markdown=True,
        default_timeout_s=60,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "wait_for_selector",
            "premium_proxy",
            "user_agent",
            "custom_headers",
            "cookies",
            "output_format",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPERAPI_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        params = _adapt(opts, self.api_key or "", url)
        headers = build_target_headers(opts)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(_ENDPOINT, params=params, headers=headers)

        raw = response.text
        json_data: dict[str, Any] | list[Any] | None = None
        html: str | None = None

        if _is_json_response(response, params):
            try:
                json_data = response.json()
            except ValueError:
                json_data = None
            pretty = (
                _json.dumps(json_data, indent=2, ensure_ascii=False)
                if json_data is not None
                else raw
            )
            text, markdown = pretty, pretty
        elif params.get("output_format") == "markdown":
            # Native markdown — do not re-derive from HTML.
            markdown = raw
            text = raw
        else:
            text, markdown = html_to_both(raw, base_url=url)
            html = raw

        meta: dict[str, object] = {"status_code": response.status_code}
        target_status = response.headers.get("sa-statuscode")
        if target_status is not None:
            meta["scraperapi_target_status"] = target_status
        credit_cost = response.headers.get("sa-credit-cost")
        if credit_cost is not None:
            meta["scraperapi_credit_cost"] = credit_cost

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            json=json_data,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=_COST_USD,
            meta=meta,
        )


__all__ = ["ScraperApiEngine"]
