"""ExaSearchEngine — neural / keyword web search via https://api.exa.ai/search.

Pure REST, no SDK. Exa returns a ``results`` array; each item carries a URL,
title, and (optionally) text or highlight snippets.

Native parameter surface
------------------------

==============================  ===============================  ==============================
Exa field                       Unified source                   Notes
==============================  ===============================  ==============================
``x-api-key`` header            ``EXA_API_KEY`` / constructor     Required auth
``query`` (body)                ``query``                         Required
``numResults`` (body)           ``opts.count``                    Result count
``type`` (body)                 constant ``"auto"``               Neural / keyword auto-select
``exa_*`` (body)                ``opts.extra["exa_*"]``           Forwarded as extra body params
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

_BASE_URL = "https://api.exa.ai"


def _build_body(query: str, opts: SearchOptions) -> dict[str, Any]:
    """Map unified ``SearchOptions`` to the Exa search request body."""
    body: dict[str, Any] = {"query": query, "numResults": opts.count, "type": "auto"}
    body.update(strip_extra_prefix(opts.extra, "exa_"))
    return body


def _snippet_for(result: dict[str, Any]) -> str:
    text = result.get("text", "") or ""
    if text:
        return str(text)
    highlights = result.get("highlights") or [""]
    return str(highlights[0]) if highlights else ""


class ExaSearchEngine(SearchEngine):
    """Exa web search engine (https://api.exa.ai/search)."""

    NAME = "exa"
    REQUIRES_API_KEY = True
    ESTIMATED_COST_USD = 0.005

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("EXA_API_KEY"))

    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        body = _build_body(query, opts)
        headers = {"x-api-key": self.api_key or "", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=float(opts.timeout_s), base_url=_BASE_URL) as client:
            response = await client.post("/search", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()

        hits: list[SearchHit] = []
        for result in data.get("results") or []:
            if not isinstance(result, dict):
                continue
            url = result.get("url")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=result.get("title", "") or "",
                    snippet=_snippet_for(result),
                    raw=dict(result),
                )
            )
        return hits


__all__ = ["ExaSearchEngine", "_build_body"]
