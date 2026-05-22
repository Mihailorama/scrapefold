"""RequestsEngine — pure async HTTP GET via httpx.

The simplest possible engine: no JS rendering, no stealth, no API key.
Free. Used as the first (cheapest) rung in nearly every ladder.
"""

from __future__ import annotations

import json
import logging

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = "scrapefold-requests/0.1"


class RequestsEngine(ScrapeEngine):
    """Async HTTP GET engine backed by httpx.

    Supports plain HTML, JSON, and plain-text responses. Non-2xx responses
    are returned as ``ScrapeResult`` (with ``meta["status_code"]``) rather
    than raising, so the router's detection layer can decide whether to
    escalate.
    """

    NAME = "requests"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=False,
        screenshot=False,
        requires_api_key=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        proxy_type="none",
        free_tier=True,
        default_timeout_s=30,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "user_agent",
            "custom_headers",
            "cookies",
            "timeout_s",
        }
    )

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* with the given options and return a ``ScrapeResult``."""
        headers: dict[str, str] = {
            "User-Agent": opts.user_agent or _DEFAULT_USER_AGENT,
        }
        if opts.language:
            headers["Accept-Language"] = opts.language

        # Caller-provided headers override defaults (including User-Agent)
        if opts.custom_headers:
            headers.update(opts.custom_headers)

        cookies = opts.cookies or {}
        timeout = float(opts.timeout_s)

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            cookies=cookies,
        ) as client:
            response = await client.get(url, headers=headers)

        content_type = response.headers.get("content-type", "")
        ct_base = content_type.split(";")[0].strip().lower()

        text_out: str
        markdown_out: str
        html_out: str | None = None
        json_out: dict | list | None = None  # type: ignore[type-arg]

        body_snippet = response.text[:512].lower()

        if ct_base == "application/json":
            # JSON response: populate json field; text/markdown = pretty-printed JSON
            json_out = response.json()
            text_out = json.dumps(json_out, ensure_ascii=False, indent=2)
            markdown_out = text_out

        elif ct_base == "text/html" or (ct_base in ("text/plain", "") and "<html" in body_snippet):
            # HTML — explicit content-type, or body-sniffed when content-type is
            # missing / generic (some servers send text/plain for HTML responses)
            raw_html = response.text
            html_out = raw_html
            text_out, markdown_out = html_to_both(raw_html, base_url=str(response.url))

        elif ct_base == "text/plain":
            text_out = response.text
            markdown_out = response.text

        else:
            # Binary or unknown content-type — leave text/markdown empty
            logger.debug(
                "engine=requests unknown content-type=%r for %s; text/markdown empty",
                content_type,
                url,
            )
            text_out = ""
            markdown_out = ""

        return ScrapeResult(
            url=str(response.url),
            text=text_out,
            markdown=markdown_out,
            html=html_out,
            json=json_out,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            meta={
                "status_code": response.status_code,
                "content_type": content_type,
                "final_url": response.url,
            },
        )


__all__ = ["RequestsEngine"]
