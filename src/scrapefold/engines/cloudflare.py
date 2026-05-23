"""CloudflareEngine — Cloudflare Browser Rendering API scrape engine.

Ported from downstream-consumer/services/url_to_text_service.py:get_content_cloudflare.
Two endpoints: /markdown (preferred — clean markdown extraction) and
/content (raw HTML fallback when /markdown returns empty or non-200).

Auth via Authorization: Bearer <CLOUDFLARE_API_TOKEN>. Account scope via
CLOUDFLARE_ACCOUNT_ID. JS rendering via render=True (default; set
render_js=False for fast static fetch).
"""

from __future__ import annotations

import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)


def _extract_markdown(payload: dict) -> str:  # type: ignore[type-arg]
    """Extract markdown from a Cloudflare /markdown response payload.

    Cloudflare returns either {"result": "<md>"} or {"result": {"markdown": "..."}}.
    Anything else collapses to empty string.
    """
    result = payload.get("result")
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        return str(result.get("markdown", "")).strip()
    return ""


def _extract_html(payload: dict) -> str:  # type: ignore[type-arg]
    """Extract raw HTML from a Cloudflare /content response payload."""
    result = payload.get("result")
    if isinstance(result, str):
        return result
    return str(result) if result is not None else ""


class CloudflareEngine(ScrapeEngine):
    """Cloudflare Browser Rendering API engine.

    Calls POST /accounts/{id}/browser-rendering/markdown first (clean
    markdown). On empty or non-200, falls back to /content (raw HTML)
    and converts via html_to_text.
    """

    NAME = "cloudflare"
    # Per-request cost — Browser Rendering is billed per request. An empty
    # /markdown that triggers the /content fallback = 2 paid requests; the
    # router's cost budget needs to account for it. Verify the exact USD
    # against Cloudflare's current pricing page at pack-open time.
    _PER_REQUEST_USD = 0.0009  # placeholder — verify and update
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=False,
        requires_api_key=True,
        estimated_cost_usd=_PER_REQUEST_USD,
        billing_unit="call",
        proxy_type="none",
        free_tier=False,
        output_native_markdown=True,
        default_timeout_s=60,
        avg_response_mb_estimate=1.0,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "render_js",
            "timeout_s",
            "extra",  # honors extra["cloudflare_skip_content_fallback"]
        }
    )

    def __init__(self, api_token: str | None = None, account_id: str | None = None) -> None:
        super().__init__(api_token or os.getenv("CLOUDFLARE_API_TOKEN"))
        self.account_id = account_id or os.getenv("CLOUDFLARE_ACCOUNT_ID")

    def is_available(self) -> bool:
        return bool(self.api_key) and bool(self.account_id)

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/browser-rendering"
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "url": url,
            "render": opts.render_js,
            "gotoOptions": {"waitUntil": "domcontentloaded"},
        }
        skip_fallback = bool(opts.extra.get("cloudflare_skip_content_fallback", False))
        # endpoint_calls is surfaced in meta so callers / billing audit can see
        # exactly which paid endpoints were hit on this scrape.
        endpoint_calls: list[str] = []
        cost_usd = 0.0

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            # Step 1: /markdown
            md_resp = await client.post(f"{base_url}/markdown", headers=headers, json=body)
            endpoint_calls.append("markdown")
            cost_usd += self._PER_REQUEST_USD

            markdown_out = ""
            if md_resp.status_code == 200:
                try:
                    markdown_out = _extract_markdown(md_resp.json())
                except ValueError:  # JSON decode failure
                    markdown_out = ""

            if markdown_out:
                return ScrapeResult(
                    url=url,
                    text=markdown_to_text(markdown_out),
                    markdown=markdown_out,
                    html=None,
                    engine=self.NAME,
                    elapsed_ms=0,  # base class fills
                    cost_usd=cost_usd,
                    meta={
                        "status_code": md_resp.status_code,
                        "endpoint_calls": endpoint_calls,
                    },
                )

            if skip_fallback:
                raise EngineError(
                    engine=self.NAME,
                    message=(
                        f"/markdown ({md_resp.status_code}) returned empty for {url}; "
                        "content fallback disabled by opts.extra"
                    ),
                    elapsed_ms=0,
                )

            # Step 2: /content fallback — second paid request
            logger.debug(
                "engine=cloudflare /markdown empty/non-200 (%d) for %s; trying /content",
                md_resp.status_code,
                url,
            )
            html_resp = await client.post(f"{base_url}/content", headers=headers, json=body)
            endpoint_calls.append("content")
            cost_usd += self._PER_REQUEST_USD

            html_out = ""
            if html_resp.status_code == 200:
                try:
                    html_out = _extract_html(html_resp.json())
                except ValueError:
                    html_out = ""

            if not html_out:
                raise EngineError(
                    engine=self.NAME,
                    message=(
                        f"both /markdown ({md_resp.status_code}) and /content "
                        f"({html_resp.status_code}) returned empty for {url}"
                    ),
                    elapsed_ms=0,
                )

            text_out, md_from_html = html_to_both(html_out, base_url=url)
            return ScrapeResult(
                url=url,
                text=text_out,
                markdown=md_from_html,
                html=html_out,
                engine=self.NAME,
                elapsed_ms=0,
                cost_usd=cost_usd,
                meta={
                    "status_code": html_resp.status_code,
                    "endpoint_calls": endpoint_calls,
                },
            )


__all__ = ["CloudflareEngine"]
