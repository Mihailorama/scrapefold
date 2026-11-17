"""SocialFetchEngine — normalized social-media JSON API (Social Fetch).

Social Fetch (https://www.socialfetch.dev, openapi https://socialfetch.dev/openapi.json)
uses resource-style paths and returns its own normalized schema, so only the
transport is shared with :class:`ScrapeCreatorsEngine`.

Pinned API contract (openapi.json, 2026-11-17):
  Method   : GET
  Base URL : https://api.socialfetch.dev
  Auth     : x-api-key: <api_key>  (keys start with ``sfk_``)
  Response : ``{data: {lookupStatus, ...}, meta: {requestId, creditsCharged}}``
  Billing  : $1.50-2.36 per 1,000 calls; not_found/private are charged.

A ``lookupStatus`` other than ``found`` raises, so the ladder escalates.

URL → endpoint routing
----------------------
============================  ====================================  ============
Target URL                     Endpoint                              Query param
============================  ====================================  ============
tiktok.com/@u/video/123        /v1/tiktok/videos                     url
tiktok.com/@u                   /v1/tiktok/profiles/<u>               —
instagram.com/p|reel|tv/<c>     /v2/instagram/media/<c>               —
instagram.com/<u>               /v2/instagram/profiles/<u>            —
youtube.com/watch|shorts        /v1/youtube/videos                    url
youtube.com/@h|/channel/…       /v1/youtube/channel                   url
(twitter|x).com/u/status/123    /v1/twitter/tweets                    url
(twitter|x).com/<u>             /v1/twitter/profiles/<u>              —
reddit.com/.../comments/…       /v1/reddit/posts                      url
============================  ====================================  ============

Escape hatch: ``extra={"socialfetch_endpoint": "/v1/linkedin/companies"}``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import quote, urlparse

from scrapefold.engines.scrapecreators import ScrapeCreatorsEngine, _route_for
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_BY_URL = {
    "/v1/tiktok/video": "/v1/tiktok/videos",
    "/v1/youtube/video": "/v1/youtube/videos",
    "/v1/youtube/channel": "/v1/youtube/channel",
    "/v1/twitter/tweet": "/v1/twitter/tweets",
    "/v1/reddit/post": "/v1/reddit/posts",
}
_BY_HANDLE = {
    "/v1/tiktok/profile": "/v1/tiktok/profiles/",
    "/v1/instagram/profile": "/v2/instagram/profiles/",
    "/v1/twitter/profile": "/v1/twitter/profiles/",
}


def _sf_route(url: str) -> tuple[str, dict[str, str]]:
    endpoint, params = _route_for(url)
    if endpoint in _BY_URL:
        return _BY_URL[endpoint], params
    if endpoint in _BY_HANDLE:
        return _BY_HANDLE[endpoint] + quote(params["handle"], safe=""), {}
    # /v1/instagram/post: the media path segment is the shortcode.
    path = urlparse(url if "://" in url else f"https://{url}").path
    m = re.search(r"/(?:p|reel|reels|tv)/([\w-]+)", path)
    if m is None:
        raise ValueError(f"socialfetch: no shortcode in {url!r}")
    return f"/v2/instagram/media/{m.group(1)}", {}


class SocialFetchEngine(ScrapeCreatorsEngine):
    """Social-media engine backed by Social Fetch (``SOCIALFETCH_API_KEY``)."""

    NAME = "socialfetch"
    CAPABILITIES = replace(ScrapeCreatorsEngine.CAPABILITIES, estimated_cost_usd=0.0024)
    BASE_URL = "https://api.socialfetch.dev"
    API_KEY_ENV = "SOCIALFETCH_API_KEY"

    def _route(self, url: str) -> tuple[str, dict[str, str]]:
        return _sf_route(url)

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        result = await super()._fetch(url, opts)
        data = result.json.get("data") if isinstance(result.json, dict) else None
        status = data.get("lookupStatus") if isinstance(data, dict) else None
        if status not in (None, "found"):
            raise ValueError(f"socialfetch lookupStatus={status} for {url!r}")
        return result


__all__ = ["SocialFetchEngine"]
