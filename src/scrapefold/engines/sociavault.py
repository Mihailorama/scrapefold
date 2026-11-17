"""SociaVaultEngine — social-media JSON API with Scrape Creators-like resources.

SociaVault (https://sociavault.com, docs https://docs.sociavault.com) mirrors the
Scrape Creators resource set under a ``/v1/scrape/`` prefix with a few renamed
resources, so this engine reuses :class:`ScrapeCreatorsEngine` with its own
routes.

Pinned API contract (docs.sociavault.com/llms.txt, 2026-11-17):
  Method   : GET
  Base URL : https://api.sociavault.com
  Auth     : X-API-Key: <api_key>  (keys start with ``sk_live_``)
  Response : ``{success, data, credits_used, endpoint}``
  Billing  : 1 credit per call; $1.99 per 1,000 on the largest pack.

URL → endpoint routing
----------------------
============================  ====================================  ============
Target URL                     Endpoint                              Query param
============================  ====================================  ============
tiktok.com/@u/video/123        /v1/scrape/tiktok/video-info          url
tiktok.com/@u                   /v1/scrape/tiktok/profile             handle
instagram.com/p|reel|tv/…       /v1/scrape/instagram/post-info        url
instagram.com/<u>               /v1/scrape/instagram/profile          handle
youtube.com/watch|shorts        /v1/scrape/youtube/video              url
youtube.com/@h|/channel/…       /v1/scrape/youtube/channel            url
(twitter|x).com/u/status/123    /v1/scrape/twitter/tweet              url
(twitter|x).com/<u>             /v1/scrape/twitter/profile            handle
reddit.com/.../comments/…       /v1/scrape/reddit/post/comments       url
============================  ====================================  ============

Escape hatch: ``extra={"sociavault_endpoint": "/v1/scrape/linkedin/company"}``.
"""

from __future__ import annotations

from dataclasses import replace

from scrapefold.engines.scrapecreators import ScrapeCreatorsEngine, _route_for

_RENAMED = {
    "/v1/tiktok/video": "/v1/scrape/tiktok/video-info",
    "/v1/instagram/post": "/v1/scrape/instagram/post-info",
    "/v1/reddit/post": "/v1/scrape/reddit/post/comments",
}


def _sv_route(url: str) -> tuple[str, dict[str, str]]:
    endpoint, params = _route_for(url)
    return _RENAMED.get(endpoint, endpoint.replace("/v1/", "/v1/scrape/", 1)), params


class SociaVaultEngine(ScrapeCreatorsEngine):
    """Social-media engine backed by SociaVault (``SOCIAVAULT_API_KEY``)."""

    NAME = "sociavault"
    CAPABILITIES = replace(ScrapeCreatorsEngine.CAPABILITIES, estimated_cost_usd=0.002)
    BASE_URL = "https://api.sociavault.com"
    API_KEY_ENV = "SOCIAVAULT_API_KEY"

    def _route(self, url: str) -> tuple[str, dict[str, str]]:
        return _sv_route(url)


__all__ = ["SociaVaultEngine"]
