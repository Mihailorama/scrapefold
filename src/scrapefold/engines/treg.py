"""Treg routed page extraction via ``treg.web.extract``.

Native input              Unified source
------------------------  ------------------------------
``url``                   ``scrape(url)``
``X-Treg-Route-Max-Cost`` ``extra['treg_max_cost_usd']`` (default $0.01)
request timeout           ``timeout_s``
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from enrichfold.treg import TregClient

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult


class TregEngine(ScrapeEngine):
    NAME = "treg"
    CAPABILITIES = EngineCapabilities(
        estimated_cost_usd=0.01,
        requires_api_key=True,
        output_native_markdown=True,
        free_tier=False,
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("TREG_TOKEN"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        response = await TregClient(self.api_key).call(
            "treg.web.extract",
            request={"url": url},
            max_cost_usd=float(opts.extra.get("treg_max_cost_usd", 0.01)),
            timeout_s=float(opts.timeout_s),
        )
        data = response.data
        output = data.get("output") if isinstance(data, Mapping) else None
        pages = output.get("pages") if isinstance(output, Mapping) else None
        if not isinstance(pages, list) or not pages:
            raise EngineError(self.NAME, "Treg web.extract returned no pages")
        page = pages[0]
        if isinstance(page, str):
            markdown = page
            text = markdown_to_text(markdown)
            html = None
            final_url = url
        elif isinstance(page, Mapping):
            page_url = page.get("final_url") or page.get("url")
            final_url = page_url if isinstance(page_url, str) else url
            page_html = page.get("html") or page.get("html_content") or page.get("rawContent")
            page_text = page.get("text")
            if page.get("format") == "html":
                page_html = page_text
            html = page_html if isinstance(page_html, str) else None
            page_markdown = (
                page.get("markdown")
                or page.get("markdown_content")
                or page.get("raw_content")
                or page.get("content")
            )
            if page.get("format") == "markdown":
                page_markdown = page_text
            markdown = page_markdown if isinstance(page_markdown, str) else ""
            text = page_text if isinstance(page_text, str) else ""
            if markdown:
                text = markdown_to_text(markdown)
            elif html:
                text, markdown = html_to_both(html, base_url=final_url)
            elif text:
                markdown = text
        else:
            raise EngineError(self.NAME, "Treg web.extract returned an unsupported page")
        if not text.strip() or not markdown.strip():
            raise EngineError(self.NAME, "Treg web.extract returned an empty page")
        return ScrapeResult(
            url=final_url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=response.cost_usd,
            meta={
                "treg_call_id": response.call_id,
                "treg_served_by": response.served_by,
            },
        )


__all__ = ["TregEngine"]
