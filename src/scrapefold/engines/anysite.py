"""AnySiteEngine — paid REST scraping via api.anysite.com.

AnySite specialises in protected targets: LinkedIn, Twitter/X, Instagram, etc.
There is no official Python SDK. This engine is a pure REST adapter over
``httpx.AsyncClient``.

Pinned API contract (as of 2026-05):
  Method   : POST
  Endpoint : https://api.anysite.com/v1/scrape
  Auth     : Authorization: Bearer <api_key>  (request header)
  Body     : JSON — see ``_adapt()`` for full field mapping.
  Response : {"data": {"html": "...", "markdown": "...",
                        "screenshot_b64": null | "<b64>"},
              "meta": {"status_code": <int>}}

Target-site headers (Accept-Language, User-Agent, Cookie, custom_headers)
are sent inside the request body under the ``headers`` and ``cookies`` keys
rather than on the AnySite HTTP call itself. This mirrors how similar
residential-proxy APIs separate "headers for AnySite" from "headers for the
target site".
"""

from __future__ import annotations

import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import (
    ScrapeOptions,
    build_target_headers,
    cookies_to_header,
    strip_extra_prefix,
)
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.anysite.com/v1/scrape"


def _adapt(opts: ScrapeOptions, url: str) -> dict:
    """Map unified ScrapeOptions to the AnySite POST JSON body.

    Target-site headers (Accept-Language, User-Agent, custom_headers) are
    collected via ``build_target_headers`` and placed in ``body["headers"]``
    so AnySite can forward them to the destination site rather than
    interpreting them on its own API call.

    Cookies are serialised to a ``"Cookie: k=v; …"`` string and placed in
    ``body["cookies"]``.

    Extra keys prefixed ``anysite_`` are stripped of the prefix and merged
    into the body at the top level (escape hatch for undocumented AnySite
    params).
    """
    body: dict = {
        "url": url,
        "render_js": opts.render_js,
        "wait_ms": opts.wait_ms,
    }

    if opts.country is not None:
        body["country"] = opts.country

    if opts.stealth:
        body["stealth"] = opts.stealth

    if opts.premium_proxy:
        body["premium_proxy"] = opts.premium_proxy

    if opts.take_screenshot:
        body["take_screenshot"] = opts.take_screenshot

    # Target-site headers: language, user-agent, custom_headers.
    # build_target_headers also handles cookies-to-Cookie-header, but we send
    # cookies separately via the body["cookies"] key, so skip that here.
    target_headers = build_target_headers(opts, include_cookies=False)
    if target_headers:
        body["headers"] = target_headers

    # Cookies become a serialised Cookie header value in the body.
    cookie_str = cookies_to_header(opts.cookies)
    if cookie_str:
        body["cookies"] = cookie_str

    # Forward anysite_* extras (prefix stripped) into the body.
    extra_params = strip_extra_prefix(opts.extra, "anysite_")
    body.update(extra_params)

    return body


class AnySiteEngine(ScrapeEngine):
    """Scraping engine backed by the AnySite REST API.

    API key is read from the constructor argument or the ``ANYSITE_API_KEY``
    environment variable. ``is_available()`` returns ``False`` when neither is set.
    """

    NAME = "anysite"
    CAPABILITIES = EngineCapabilities(
        requires_api_key=True,
        estimated_cost_usd=0.002,
        billing_unit="call",
        proxy_type="residential",
        js_rendering=True,
        stealth=True,
        output_native_markdown=True,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "wait_ms",
            "stealth",
            "premium_proxy",
            "user_agent",
            "custom_headers",
            "cookies",
            "output_format",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("ANYSITE_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* via the AnySite API and return a ``ScrapeResult``."""
        body = _adapt(opts, url)
        headers = {
            "Authorization": f"Bearer {self.api_key or ''}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(_ENDPOINT, json=body, headers=headers)

        # Surface upstream failures (401 bad key, 429 throttle, 5xx) — without
        # this, the engine returned an empty ScrapeResult for any error and
        # the router could not distinguish "blank page" from "API down".
        response.raise_for_status()

        payload = response.json()
        data = payload.get("data", {})
        meta_block = payload.get("meta", {})

        raw_html: str | None = data.get("html") or None
        raw_markdown: str | None = data.get("markdown") or None
        screenshot_b64: str | None = data.get("screenshot_b64") or None

        # Populate text and markdown from whichever form the engine returned.
        # Golden rule: both slots must always be non-empty when scrape succeeds.
        text_out: str
        markdown_out: str
        html_out: str | None = raw_html

        if raw_html:
            text_out, markdown_out = html_to_both(raw_html, base_url=url)
        elif raw_markdown:
            markdown_out = raw_markdown
            text_out = markdown_to_text(raw_markdown)
            html_out = None
        else:
            text_out = ""
            markdown_out = ""
            html_out = None

        upstream_status = meta_block.get("status_code")

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=html_out,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            screenshot_b64=screenshot_b64,
            meta={"status_code": upstream_status},
        )


__all__ = ["AnySiteEngine"]
