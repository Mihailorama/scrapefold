"""SerperSearchEngine — Google SERP via https://google.serper.dev/search.

Pure REST, no SDK. Serper returns Google's organic results as JSON. Cheap and
fast; the default paid engine when a ``SERPER_API_KEY`` is present.

Native parameter surface
------------------------

==============================  ===============================  ==============================
Serper field                    Unified source                   Notes
==============================  ===============================  ==============================
``X-API-KEY`` header            ``SERPER_API_KEY`` / constructor  Required auth
``q`` (body)                    ``query``                         Required
``num`` (body)                  ``opts.count``                    Result count
``gl`` (body)                   ``opts.country``                  Omitted when None
``hl`` (body)                   ``opts.language``                 Omitted when None
``serper_*`` (body)             ``opts.extra["serper_*"]``        Forwarded as extra body params
==============================  ===============================  ==============================
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from scrapefold.options import strip_extra_prefix
from scrapefold.search.engines.base import SearchEngine
from scrapefold.search.options import SearchOptions
from scrapefold.search.types import SearchHit

logger = logging.getLogger(__name__)

_ENDPOINT = "https://google.serper.dev/search"


def _build_body(query: str, opts: SearchOptions) -> dict[str, Any]:
    """Map unified ``SearchOptions`` to the Serper search request body."""
    body: dict[str, Any] = {"q": query, "num": opts.count}
    if opts.country is not None:
        body["gl"] = opts.country
    if opts.language is not None:
        body["hl"] = opts.language
    body.update(strip_extra_prefix(opts.extra, "serper_"))
    return body


class SerperSearchEngine(SearchEngine):
    """Serper Google SERP engine (https://google.serper.dev/search)."""

    NAME = "serper"
    REQUIRES_API_KEY = True
    ESTIMATED_COST_USD = 0.001

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SERPER_API_KEY"))

    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        body = _build_body(query, opts)
        headers = {"X-API-KEY": self.api_key or "", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(_ENDPOINT, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        hits: list[SearchHit] = []
        for item in data.get("organic") or []:
            if not isinstance(item, dict):
                continue
            url = item.get("link")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title", "") or "",
                    snippet=item.get("snippet", "") or "",
                    raw=dict(item),
                )
            )
        return hits


__all__ = ["SerperSearchEngine", "_build_body"]
