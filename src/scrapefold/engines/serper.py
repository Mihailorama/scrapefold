"""SerperEngine — paid webpage scrape via https://scrape.serper.dev.

Pure REST, no SDK. Serper's scrape endpoint renders the page server-side and
returns clean plain text plus native markdown, head metadata, and any JSON-LD
structured data. Ideal cheap markdown source for non-anti-bot targets.

Native parameter surface
------------------------

==============================  ===============================  ==============================
Serper field                    Unified source                   Notes
==============================  ===============================  ==============================
``X-API-KEY`` header            ``SERPER_API_KEY`` / constructor  Required auth
``url`` (body)                  target URL                        Required
``includeMarkdown`` (body)      ``output_format``                 ``False`` when output_format="text"
``serper_*`` (body)             ``opts.extra["serper_*"]``        Forwarded as extra body params
==============================  ===============================  ==============================

Response fields: ``text``, ``markdown``, ``metadata`` (head meta), ``jsonld``
(structured data), ``credits``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import markdown_to_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://scrape.serper.dev"
_USD_PER_CREDIT = 0.001  # standard-tier estimate; a markdown scrape costs 2 credits


def _build_body(url: str, opts: ScrapeOptions) -> dict[str, Any]:
    """Map unified ScrapeOptions to the Serper scrape request body."""
    body: dict[str, Any] = {
        "url": url,
        "includeMarkdown": opts.output_format != "text",
    }
    body.update(strip_extra_prefix(opts.extra, "serper_"))
    return body


class SerperEngine(ScrapeEngine):
    """Serper scrape engine (https://scrape.serper.dev).

    POSTs the target URL and returns Serper's native text + markdown, with
    JSON-LD structured data surfaced in ``ScrapeResult.json`` when present.
    """

    NAME = "serper"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        estimated_cost_usd=2 * _USD_PER_CREDIT,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        output_native_markdown=True,
        free_tier=True,  # 2 500 free credits on signup
        default_timeout_s=60,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SERPER_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Call Serper scrape and map the response to a ``ScrapeResult``."""
        body = _build_body(url, opts)
        headers = {"X-API-KEY": self.api_key or "", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(_ENDPOINT, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        markdown = data.get("markdown") or ""
        text = data.get("text") or ""
        # Serper returns both; if only one is present, derive the other.
        if markdown and not text:
            text = markdown_to_text(markdown)
        elif text and not markdown:
            markdown = text

        jsonld = data.get("jsonld") or None

        credits = data.get("credits")
        cost = credits * _USD_PER_CREDIT if isinstance(credits, (int, float)) else 0.0

        meta: dict[str, Any] = {"status_code": response.status_code}
        if isinstance(data.get("metadata"), dict):
            meta.update(data["metadata"])
        if credits is not None:
            meta["serper_credits"] = credits

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=None,
            json=jsonld,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=cost,
            meta=meta,
        )


__all__ = ["SerperEngine", "_build_body"]
