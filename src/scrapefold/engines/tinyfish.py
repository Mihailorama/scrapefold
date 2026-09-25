"""TinyFish Fetch API for one URL; native Markdown, HTML or document JSON."""

from __future__ import annotations

import json
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult


class TinyFishEngine(ScrapeEngine):
    NAME = "tinyfish"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        requires_api_key=True,
        output_native_markdown=True,
        free_tier=True,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("TINYFISH_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        fmt = opts.output_format if opts.output_format in {"html", "json"} else "markdown"
        body = {"urls": [url], "format": fmt}
        body.update(strip_extra_prefix(opts.extra, "tinyfish_"))
        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(
                "https://api.fetch.tinyfish.ai",
                json=body,
                headers={"X-API-Key": self.api_key or ""},
            )
            response.raise_for_status()
            data = response.json()
        results = data.get("results") or []
        if not results:
            errors = data.get("errors") or []
            raise EngineError(
                self.NAME, f"fetch failed: {errors[0].get('error') if errors else 'empty response'}"
            )
        page = results[0]
        content = page.get("text")
        if fmt == "html":
            html = content if isinstance(content, str) else ""
            text, markdown = html_to_both(html, base_url=url)
            json_data = None
        elif fmt == "json":
            json_data = content if isinstance(content, (dict, list)) else None
            text = markdown = json.dumps(json_data) if json_data is not None else ""
            html = None
        else:
            markdown = content if isinstance(content, str) else ""
            text = markdown_to_text(markdown)
            html = json_data = None
        return ScrapeResult(
            url=page.get("final_url") or url,
            text=text,
            markdown=markdown,
            html=html,
            json=json_data,
            engine=self.NAME,
            elapsed_ms=0,
            meta={"title": page.get("title")},
        )
