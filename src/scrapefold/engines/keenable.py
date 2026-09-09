"""Keenable search and clean-markdown page fetch over its REST API.

Native parameter surface
------------------------

========================  =================================  =============================
Keenable field            Unified source                     Notes
========================  =================================  =============================
``X-API-Key``             ``KEENABLE_API_KEY`` / constructor  Optional; raises rate limits
``url``                   target URL                          Fetch mode (default)
``max_chars``             ``keenable_max_chars``              Fetch mode
``live``                  ``keenable_live``                   Fetch mode
``prompt``                ``keenable_prompt``                 Fetch mode
``query``                 target / ``keenable_query``          Search mode
search filters            matching ``keenable_*`` extras      Search mode
========================  =================================  =============================

Set ``opts.extra["keenable_mode"] = "search"`` for search. Without a key,
the same requests use Keenable's public endpoints with the required app title.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text, markdown_to_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

_BASE_URL = "https://api.keenable.ai"
_COST_PER_CALL_USD = 0.004
_SEARCH_FIELDS = (
    "site",
    "acquired_after",
    "acquired_before",
    "published_after",
    "published_before",
    "query_time",
    "snippet_max_length",
    "max_results",
)


def _build_request(url: str, opts: ScrapeOptions) -> tuple[str, dict[str, Any]]:
    extra = strip_extra_prefix(opts.extra, "keenable_")
    mode = str(extra.get("mode", "fetch"))
    if mode == "fetch":
        return mode, {"url": url} | {
            key: extra[key] for key in ("max_chars", "live", "prompt") if key in extra
        }
    if mode == "search":
        return mode, {"query": str(extra.get("query", url))} | {
            key: extra[key] for key in _SEARCH_FIELDS if key in extra
        }
    raise ValueError(f"unknown keenable_mode: {mode!r}")


class KeenableEngine(ScrapeEngine):
    """Keenable REST engine; works keyed or via the rate-limited public API."""

    NAME = "keenable"
    CAPABILITIES = EngineCapabilities(
        estimated_cost_usd=_COST_PER_CALL_USD,
        requires_api_key=False,
        output_native_markdown=True,
        free_tier=True,
        avg_response_mb_estimate=0.5,
    )
    SUPPORTED_OPTIONS = frozenset({"timeout_s", "extra"})

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        super().__init__(api_key or os.getenv("KEENABLE_API_KEY"))
        self.base_url = (base_url or os.getenv("KEENABLE_API_URL") or _BASE_URL).rstrip("/")

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        mode, payload = _build_request(url, opts)
        suffix = "" if self.api_key else "/public"
        headers = (
            {"X-API-Key": self.api_key} if self.api_key else {"X-Keenable-Title": "scrapefold"}
        )

        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=float(opts.timeout_s)
        ) as client:
            if mode == "fetch":
                response = await client.get(f"/v1/fetch{suffix}", params=payload, headers=headers)
            else:
                response = await client.post(f"/v1/search{suffix}", json=payload, headers=headers)
            if response.is_error:
                try:
                    error = response.json()
                except ValueError:
                    error = {}
                detail = error.get("message") or error.get("error") or response.text
                raise EngineError(self.NAME, f"HTTP {response.status_code}: {detail}")
            data = response.json()

        if mode == "fetch":
            markdown = str(data.get("content") or "")
            text = markdown_to_text(markdown)
        else:
            text, markdown = json_to_scrape_text(data)

        return ScrapeResult(
            url=str(data.get("url") or url),
            text=text,
            markdown=markdown,
            html=None,
            json=data,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=_COST_PER_CALL_USD if self.api_key else 0.0,
            meta={
                "status_code": response.status_code,
                "keenable_mode": mode,
                **{
                    key: data[key]
                    for key in ("title", "description", "author", "published_at")
                    if data.get(key) is not None
                },
            },
        )


__all__ = ["KeenableEngine", "_build_request"]
