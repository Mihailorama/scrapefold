"""Linkup Fetch API for one URL; clean Markdown and optional structured data."""

from __future__ import annotations

import json
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult


class LinkupEngine(ScrapeEngine):
    NAME = "linkup"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        requires_api_key=True,
        output_native_markdown=True,
        free_tier=False,
    )
    SUPPORTED_OPTIONS = frozenset({"render_js", "output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("LINKUP_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        body = {"url": url, "renderJs": opts.render_js, "includeRawContent": True}
        if opts.output_format == "json" and isinstance(opts.extra.get("schema"), dict):
            body["schema"] = json.dumps(opts.extra["schema"])
        body.update(strip_extra_prefix(opts.extra, "linkup_"))
        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(
                "https://api.linkup.so/v1/fetch",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key or ''}"},
            )
            response.raise_for_status()
            data = response.json()
        markdown = data.get("markdown") or ""
        html = data.get("rawContent") if data.get("contentType") == "html" else None
        if html and not markdown:
            text, markdown = html_to_both(html, base_url=url)
        else:
            text = markdown_to_text(markdown)
        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            json=data.get("data"),
            engine=self.NAME,
            elapsed_ms=0,
        )
