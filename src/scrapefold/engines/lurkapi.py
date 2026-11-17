"""LurkAPIEngine — ScrapeCreators-compatible social-media API at about half the price.

LurkAPI (https://lurkapi.com) serves 69 Scrape Creators endpoints at the same
paths with the same ``x-api-key`` header (https://lurkapi.com/docs/switching),
so this engine reuses :class:`ScrapeCreatorsEngine` and only changes the base
URL, the key and the routes that differ.

Pinned API contract (openapi.json, 2026-11-17):
  Method   : GET
  Base URL : https://api.lurkapi.com
  Auth     : x-api-key: <api_key>  (keys start with ``lk_live_``)
  Response : ``{success, credits_remaining, credits_charged, ...}``
  Billing  : 1 credit per social call; $0.59-0.94 per 1,000 credits.
             Invalid params, 401/402/429 and 5xx are free.

Routes that differ from Scrape Creators
---------------------------------------
============================  ===========================  ==================
Target URL                     Endpoint                     Query param
============================  ===========================  ==================
tiktok.com/@u/video/123        /v2/tiktok/video             url=<full url>
instagram.com/<u>               /v1/instagram/profile        username=<u>
reddit.com/.../comments/…       /v1/reddit/post/comments     url=<full url>
============================  ===========================  ==================

Every other route, and the escape hatch, is the Scrape Creators one; extras use
the ``lurkapi_`` prefix (``extra={"lurkapi_endpoint": "/v1/threads/post"}``).
"""

from __future__ import annotations

from dataclasses import replace

from scrapefold.engines.scrapecreators import ScrapeCreatorsEngine, _route_for

_RENAMED = {
    "/v1/tiktok/video": "/v2/tiktok/video",
    "/v1/reddit/post": "/v1/reddit/post/comments",
}


def _lurk_route(url: str) -> tuple[str, dict[str, str]]:
    endpoint, params = _route_for(url)
    if endpoint == "/v1/instagram/profile":
        params = {"username": params["handle"]}
    return _RENAMED.get(endpoint, endpoint), params


class LurkAPIEngine(ScrapeCreatorsEngine):
    """Scrape Creators-compatible engine backed by LurkAPI (``LURKAPI_API_KEY``)."""

    NAME = "lurkapi"
    CAPABILITIES = replace(ScrapeCreatorsEngine.CAPABILITIES, estimated_cost_usd=0.00094)
    BASE_URL = "https://api.lurkapi.com"
    API_KEY_ENV = "LURKAPI_API_KEY"

    def _route(self, url: str) -> tuple[str, dict[str, str]]:
        return _lurk_route(url)


__all__ = ["LurkAPIEngine"]
