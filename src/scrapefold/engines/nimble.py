"""Nimble v2 Extract API for rendered HTML and Markdown."""

from __future__ import annotations

import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult


class NimbleEngine(ScrapeEngine):
    NAME = "nimble"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        requires_api_key=True,
        proxy_type="residential",
        free_tier=False,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "render_js",
            "stealth",
            "country",
            "language",
            "user_agent",
            "custom_headers",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("NIMBLE_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        body: dict[str, Any] = {
            "url": url,
            "render": opts.render_js or opts.stealth,
            "formats": ["html", "markdown"],
        }
        if opts.stealth:
            body["driver"] = "vx10"
        if opts.country:
            body["country"] = opts.country
        if opts.language:
            body["locale"] = opts.language
        if opts.take_screenshot:
            body["formats"].append("screenshot")
        headers = build_target_headers(opts)
        if headers:
            body["headers"] = headers
        body.update(strip_extra_prefix(opts.extra, "nimble_"))
        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(
                "https://sdk.nimbleway.com/v2/extract",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key or ''}"},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "success":
            raise EngineError(self.NAME, f"extract status: {payload.get('status')}")
        data = payload.get("data") or {}
        html = data.get("html") or None
        markdown = data.get("markdown") or ""
        if markdown:
            text = markdown_to_text(markdown)
        elif html:
            text, markdown = html_to_both(html, base_url=url)
        else:
            raise EngineError(self.NAME, "extract returned no page content")
        return ScrapeResult(
            url=payload.get("url") or url,
            text=text,
            markdown=markdown,
            html=html,
            screenshot_b64=data.get("screenshot"),
            engine=self.NAME,
            elapsed_ms=0,
            meta={"status_code": payload.get("status_code"), "task_id": payload.get("task_id")},
        )
