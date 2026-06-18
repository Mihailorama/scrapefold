"""ExaEngine — paid REST search, contents, answer, and agent workflows.

Pure REST, no SDK. Exa exposes several endpoint shapes under one vendor API,
so this engine keeps one registry name (``exa``) and selects native endpoints
with ``opts.extra["exa_mode"]``.

Native parameter surface
------------------------

==============================  ===============================  ==============================
Exa field                       Unified source                   Notes
==============================  ===============================  ==============================
``x-api-key``                   ``EXA_API_KEY`` / constructor     Required auth header
``/contents.urls``              target URL / ``exa_urls``         Default non-LinkedIn mode
``/contents.text``              ``exa_text``                      Defaults to ``True``
``/contents.highlights``        ``exa_highlights``                Top-level on contents
``/contents.summary``           ``exa_summary``                   Top-level on contents
``/search.query``               ``exa_query`` / target URL        LinkedIn gets URL-oriented query
``/search.type``                ``exa_type``                      Defaults to ``auto``
``/search.category``            ``exa_category`` / LinkedIn URL   ``people`` or ``company``
``/search.contents``            ``exa_contents``                  Defaults to highlights
``/findSimilar.url``            target URL / ``exa_url``          Explicit mode only
``/answer.text``                ``exa_text``                      Defaults to ``True``
``/agent/runs.effort``          ``exa_effort``                    Explicit mode only
==============================  ===============================  ==============================

Streaming responses are intentionally out of scope for the first pass.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exa.ai"
_DEFAULT_COST_USD = 0.005
_TERMINAL_AGENT_STATUSES = frozenset({"completed", "failed", "cancelled"})
_MODE_PATHS = {
    "contents": "/contents",
    "search": "/search",
    "find_similar": "/findSimilar",
    "answer": "/answer",
    "agent": "/agent/runs",
}
_CATEGORY_UNSUPPORTED_FILTERS = frozenset(
    {
        "excludeDomains",
        "startPublishedDate",
        "endPublishedDate",
        "startCrawlDate",
        "endCrawlDate",
    }
)


def _parsed(url: str):
    return urlparse(url if "://" in url else f"https://{url}")


def _path(url: str) -> str:
    return _parsed(url).path


def _host(url: str) -> str:
    return _parsed(url).netloc.lower()


def _is_linkedin_profile(url: str) -> bool:
    return "linkedin.com" in _host(url) and "/in/" in _path(url)


def _is_linkedin_company(url: str) -> bool:
    return "linkedin.com" in _host(url) and "/company/" in _path(url)


def _infer_mode(url: str, opts: ScrapeOptions) -> str:
    extra = strip_extra_prefix(opts.extra, "exa_")
    mode = extra.get("mode")
    if mode is not None:
        return str(mode)
    if _is_linkedin_profile(url) or _is_linkedin_company(url):
        return "search"
    return "contents"


def _query_for(url: str, extra: dict[str, Any]) -> str:
    if "query" in extra:
        return str(extra["query"])
    if "linkedin.com" in _host(url):
        return f"public LinkedIn profile or company page {url}"
    return url


def _drop_unsupported_category_filters(payload: dict[str, Any]) -> None:
    category = payload.get("category")
    if category not in {"people", "company"}:
        return
    for key in _CATEGORY_UNSUPPORTED_FILTERS:
        if key in payload:
            logger.debug("exa category=%s dropping unsupported filter=%s", category, key)
            payload.pop(key, None)
    if category == "people" and "includeDomains" in payload:
        logger.debug("exa category=people dropping unsupported filter=includeDomains")
        payload.pop("includeDomains", None)


def _build_contents_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "urls": extra.get("urls", [url]),
        "text": extra.get("text", True),
    }
    for key in (
        "highlights",
        "summary",
        "subpages",
        "subpageTarget",
        "extras",
        "maxAgeHours",
        "livecrawlTimeout",
    ):
        if key in extra:
            payload[key] = extra[key]
    return payload


def _build_search_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": _query_for(url, extra),
        "type": extra.get("type", "auto"),
        "numResults": int(extra.get("numResults", 10)),
        "contents": extra.get("contents", {"highlights": True}),
    }
    if _is_linkedin_profile(url):
        payload.setdefault("category", "people")
    elif _is_linkedin_company(url):
        payload.setdefault("category", "company")

    for key in (
        "category",
        "includeDomains",
        "excludeDomains",
        "startPublishedDate",
        "endPublishedDate",
        "startCrawlDate",
        "endCrawlDate",
        "additionalQueries",
        "systemPrompt",
        "outputSchema",
        "maxAgeHours",
        "moderation",
    ):
        if key in extra:
            payload[key] = extra[key]
    _drop_unsupported_category_filters(payload)
    return payload


def _build_find_similar_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": str(extra.get("url", url)),
        "numResults": int(extra.get("numResults", 10)),
        "contents": extra.get("contents", {"highlights": True}),
    }


def _build_answer_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    if extra.get("stream"):
        raise ValueError("exa answer streaming is not supported by ExaEngine yet")
    payload: dict[str, Any] = {"query": _query_for(url, extra), "text": extra.get("text", True)}
    if "outputSchema" in extra:
        payload["outputSchema"] = extra["outputSchema"]
    return payload


def _build_agent_payload(url: str, extra: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": _query_for(url, extra)}
    for key in ("outputSchema", "effort", "input", "previousRunId"):
        if key in extra:
            payload[key] = extra[key]
    return payload


def _build_payload(mode: str, url: str, opts: ScrapeOptions) -> tuple[str, dict[str, Any]]:
    extra = strip_extra_prefix(opts.extra, "exa_")
    extra.pop("mode", None)
    if mode == "contents":
        return _MODE_PATHS[mode], _build_contents_payload(url, extra)
    if mode == "search":
        return _MODE_PATHS[mode], _build_search_payload(url, extra)
    if mode == "find_similar":
        return _MODE_PATHS[mode], _build_find_similar_payload(url, extra)
    if mode == "answer":
        return _MODE_PATHS[mode], _build_answer_payload(url, extra)
    if mode == "agent":
        return _MODE_PATHS[mode], _build_agent_payload(url, extra)
    raise ValueError(f"unknown exa_mode: {mode!r}")


def _cost_from_payload(payload: dict[str, Any]) -> float:
    cost = payload.get("costDollars")
    if isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, (int, float)):
            return float(total)
    return _DEFAULT_COST_USD


def _validate_contents_payload(payload: dict[str, Any]) -> None:
    if payload.get("results"):
        return
    statuses = payload.get("statuses")
    if (
        isinstance(statuses, list)
        and statuses
        and all(isinstance(s, dict) and s.get("status") != "success" for s in statuses)
    ):
        raise ValueError("exa contents returned no successful contents")


def _result_from_payload(url: str, payload: dict[str, Any]) -> ScrapeResult:
    text_out, markdown_out = json_to_scrape_text(payload)
    return ScrapeResult(
        url=url,
        text=text_out,
        markdown=markdown_out,
        html=None,
        json=payload,
        engine=ExaEngine.NAME,
        elapsed_ms=0,
        cost_usd=_cost_from_payload(payload),
        meta={"status_code": 200, "request_id": payload.get("requestId")},
    )


class ExaEngine(ScrapeEngine):
    """Exa REST engine for search, contents, similar pages, answers, and Agent runs."""

    NAME = "exa"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_DEFAULT_COST_USD,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
        output_native_markdown=False,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("EXA_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        mode = _infer_mode(url, opts)
        path, payload = _build_payload(mode, url, opts)
        headers = {"x-api-key": self.api_key or "", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=float(opts.timeout_s), base_url=_BASE_URL) as client:
            if mode == "agent":
                response = await client.post(path, json=payload, headers=headers)
                response.raise_for_status()
                run = await self._poll_agent_run(client, response.json(), opts, headers)
                return _result_from_payload(url, run)

            response = await client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            if mode == "contents":
                _validate_contents_payload(data)
            return _result_from_payload(url, data)

    async def _poll_agent_run(
        self,
        client: httpx.AsyncClient,
        initial: dict[str, Any],
        opts: ScrapeOptions,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        run_id = initial.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("exa agent run response did not include an id")

        extra = strip_extra_prefix(opts.extra, "exa_")
        interval_ms = int(extra.get("poll_interval_ms", 1000))
        max_poll_ms = int(extra.get("max_poll_ms", max(opts.timeout_s * 1000, 1000)))
        waited_ms = 0
        current = initial

        while str(current.get("status")) not in _TERMINAL_AGENT_STATUSES:
            if waited_ms >= max_poll_ms:
                raise TimeoutError(f"exa agent run {run_id} did not finish within {max_poll_ms}ms")
            if interval_ms > 0:
                await asyncio.sleep(interval_ms / 1000)
            waited_ms += interval_ms
            response = await client.get(f"/agent/runs/{run_id}", headers=headers)
            response.raise_for_status()
            current = response.json()

        if current.get("status") != "completed":
            raise ValueError(f"exa agent run {run_id} ended with status={current.get('status')}")
        return current


__all__ = [
    "ExaEngine",
    "_build_payload",
    "_infer_mode",
    "_result_from_payload",
]
