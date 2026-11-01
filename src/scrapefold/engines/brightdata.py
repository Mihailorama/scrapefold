"""Bright Data Web Unlocker engine (synchronous REST API).

Registered as ``brightdata_unlocker_sync`` — the last-resort step of most
ladders — with the user-facing alias ``brightdata``. Before 2026-11-01 the
ladders referenced this name but no engine existed, so the router silently
skipped the final tier (SCRAPEFOLD-7).

API (docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website,
checked 2026-11-01): ``POST https://api.brightdata.com/request`` with
``Authorization: Bearer <key>``; body ``{zone, url, format}``. With
``format="json"`` the reply is ``{status_code, headers, body}``, which keeps the
target's status and headers for detection and reclassification.

Config: ``BRIGHTDATA_API_KEY`` (or the constructor ``api_key``) and the Web
Unlocker zone name in ``BRIGHTDATA_UNLOCKER_ZONE`` (default ``web_unlocker1``,
Bright Data's default zone name) or ``extra["brightdata_zone"]``.

Native parameter surface:

| Native      | Unified option                   | Default |
|-------------|----------------------------------|---------|
| ``country`` | ``country`` (ISO-2, lower-cased) | zone default |
| ``render``  | ``extra["brightdata_render"]``   | off (forces a browser, slower) |
| ``zone``    | ``extra["brightdata_zone"]``     | ``$BRIGHTDATA_UNLOCKER_ZONE`` |
| timeout     | ``timeout_s``                    | 60 |
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.brightdata.com/request"
_DEFAULT_ZONE = "web_unlocker1"


def _adapt(opts: ScrapeOptions, url: str) -> dict[str, Any]:
    """Map unified options to the ``/request`` JSON body."""
    body: dict[str, Any] = {
        "zone": opts.extra.get("brightdata_zone")
        or os.getenv("BRIGHTDATA_UNLOCKER_ZONE")
        or _DEFAULT_ZONE,
        "url": url,
        "format": "json",
    }
    if opts.country:
        body["country"] = opts.country.lower()
    if opts.extra.get("brightdata_render"):
        body["render"] = "true"
    return body


class BrightDataUnlockerEngine(ScrapeEngine):
    """Bright Data Web Unlocker — paid last-resort unblocking."""

    NAME = "brightdata_unlocker_sync"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=False,
        estimated_cost_usd=0.0015,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="residential",
        default_timeout_s=60,
        avg_response_mb_estimate=3.0,
        bills_failed_attempts=True,  # conservative budget accounting (P1 #4 invariant)
    )
    SUPPORTED_OPTIONS = frozenset({"country", "render_js", "timeout_s", "output_format", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("BRIGHTDATA_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        body = _adapt(opts, url)
        logger.debug("brightdata fetch url=%s zone=%s", url, body["zone"])
        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(
                _ENDPOINT, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        if response.status_code >= 400:
            # API-level failure (bad key/zone, quota): the reason is in x-brd-error.
            reason = response.headers.get("x-brd-error") or response.text[:200]
            raise RuntimeError(f"Bright Data API {response.status_code}: {reason}")

        data = response.json()
        html = data.get("body") or ""
        headers = {str(k).lower(): str(v) for k, v in (data.get("headers") or {}).items()}
        text, markdown = html_to_both(html, base_url=url)
        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            meta={"status_code": data.get("status_code"), "headers": headers},
        )


__all__ = ["BrightDataUnlockerEngine", "_adapt"]
