"""Browserbase Fetch API for retrieving one URL without a browser session.

Native options: ``url`` from the scrape target, ``allowRedirects=True`` by
default, and ``browserbase_*`` keys from ``ScrapeOptions.extra``.
"""

from __future__ import annotations

import os

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult


class BrowserbaseEngine(ScrapeEngine):
    NAME = "browserbase"
    CAPABILITIES = EngineCapabilities(requires_api_key=True, free_tier=False)
    SUPPORTED_OPTIONS = frozenset({"timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("BROWSERBASE_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        body = {"url": url, "allowRedirects": True}
        body.update(strip_extra_prefix(opts.extra, "browserbase_"))
        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(
                "https://api.browserbase.com/v1/fetch",
                json=body,
                headers={"X-BB-API-Key": self.api_key or ""},
            )
            response.raise_for_status()
            data = response.json()
        status = data.get("statusCode")
        if isinstance(status, int) and status >= 400:
            raise EngineError(self.NAME, f"target returned HTTP {status}")
        content = data.get("content") or ""
        if not content.strip():
            raise EngineError(self.NAME, "fetch returned no page content")
        content_type = data.get("contentType") or ""
        if "html" in content_type.lower():
            text, markdown = html_to_both(content, base_url=url)
            html = content
        else:
            text = markdown = content
            html = None
        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,
            meta={"status_code": status, "content_type": content_type},
        )
