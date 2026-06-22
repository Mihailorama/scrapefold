"""SocialCrawlEngine -- paid REST gateway for social-media data.

SocialCrawl (https://www.socialcrawl.dev) exposes JSON endpoints across social
platforms and adjacent public-data sources. This engine is pure REST over
``httpx.AsyncClient`` and returns the vendor's JSON envelope in
``ScrapeResult.json`` while post-converting it to text/markdown.

Pinned API contract (as of 2026-06):
  Method   : GET
  Base URL : https://www.socialcrawl.dev/v1
  Auth     : x-api-key: <api_key>  (request header)
  Response : JSON envelope with success/platform/endpoint/data/credits/request_id.

URL -> endpoint routing
-----------------------
The normal ``scrape(url)`` path maps common public URLs to default SocialCrawl
endpoints:

============================  =========================  ==================
Target URL                     Endpoint                   Query param
============================  =========================  ==================
tiktok.com/@u/video/123        /tiktok/post               url=<full url>
tiktok.com/@u                  /tiktok/profile            handle=<u>
instagram.com/p|reel|tv/...    /instagram/post            url=<full url>
instagram.com/<u>              /instagram/profile         handle=<u>
youtube.com/watch|shorts,      /youtube/video             url=<full url>
  youtu.be/...
youtube.com/@h                 /youtube/channel           handle=<h>
facebook.com/.../posts/...     /facebook/post             url=<full url>
facebook.com/<page>            /facebook/profile          url=<full url>
(twitter|x).com/u/status/123   /twitter/tweet             url=<full url>
(twitter|x).com/<u>            /twitter/profile           handle=<u>
linkedin.com/in/...            /linkedin/profile          url=<full url>
linkedin.com/company/...       /linkedin/company          url=<full url>
linkedin.com/posts|pulse/...   /linkedin/post             url=<full url>
reddit.com/.../comments/...    /reddit/post/comments      url=<full url>
============================  =========================  ==================

Gateway mode
------------
Set ``opts.extra["socialcrawl_endpoint"]`` to force any SocialCrawl endpoint
path, for example ``"/instagram/profile"``. A leading ``/v1`` is accepted and
normalized away because ``_BASE_URL`` already includes the version prefix.
Every other ``socialcrawl_*`` extra is stripped of its prefix and merged into
the query string. When an endpoint is forced, ``url`` is added automatically
only if no target parameter is supplied through extras.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.socialcrawl.dev/v1"
_COST_PER_CALL = 0.002

_TARGET_PARAMS = frozenset(
    {
        "url",
        "handle",
        "user_id",
        "userId",
        "channelId",
        "id",
        "query",
        "subreddit",
        "playlist_id",
        "hashtag",
        "clipId",
        "pageId",
        "company",
        "companyId",
        "companyName",
    }
)


def _host(url: str) -> str:
    """Return the lowercased host of ``url`` without a leading ``www.``."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _path(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").path


def _first_segment(path: str) -> str | None:
    for seg in path.split("/"):
        if seg:
            return seg
    return None


def _tiktok_handle(path: str) -> str | None:
    m = re.search(r"@([\w.\-]+)", path)
    return m.group(1) if m else None


def _youtube_handle(path: str) -> str | None:
    m = re.search(r"/@([\w.\-]+)", path)
    return m.group(1) if m else None


def _twitter_handle(path: str) -> str | None:
    first = _first_segment(path)
    if first in {None, "i", "intent", "search", "share"}:
        return None
    return first


def _normalize_endpoint(endpoint: object) -> str:
    """Normalize a user-supplied endpoint to a path under ``_BASE_URL``."""
    path = str(endpoint).strip()
    if not path:
        raise ValueError("socialcrawl_endpoint must not be empty")
    if not path.startswith("/"):
        path = f"/{path}"
    if path == "/v1":
        raise ValueError("socialcrawl_endpoint must include a path after /v1")
    if path.startswith("/v1/"):
        path = path[3:]
    return path


def _route_for(url: str) -> tuple[str, dict[str, str]]:
    """Map a target URL to ``(endpoint_path, query_params)``.

    Raises ``ValueError`` when the URL matches no known SocialCrawl platform.
    """
    host = _host(url)
    path = _path(url)

    if "tiktok.com" in host:
        if "/video/" in path:
            return "/tiktok/post", {"url": url}
        handle = _tiktok_handle(path)
        if handle:
            return "/tiktok/profile", {"handle": handle}

    elif "instagram.com" in host:
        if re.search(r"/(p|reel|reels|tv)/", path):
            return "/instagram/post", {"url": url}
        handle = _first_segment(path)
        if handle:
            return "/instagram/profile", {"handle": handle}

    elif "youtube.com" in host or "youtu.be" in host:
        if "youtu.be" in host or re.search(r"/(watch|shorts)\b", path) or "watch?" in url:
            return "/youtube/video", {"url": url}
        handle = _youtube_handle(path)
        if handle:
            return "/youtube/channel", {"handle": handle}
        if path.startswith("/channel/"):
            channel_id = path.split("/")[2]
            return "/youtube/channel", {"channelId": channel_id}
        return "/youtube/channel", {"url": url}

    elif "facebook.com" in host or "fb.com" in host:
        if re.search(r"/(posts|videos|reel|reels|watch)/", path):
            return "/facebook/post", {"url": url}
        return "/facebook/profile", {"url": url}

    elif host in {"twitter.com", "x.com"} or host.endswith((".twitter.com", ".x.com")):
        if "/status/" in path:
            return "/twitter/tweet", {"url": url}
        handle = _twitter_handle(path)
        if handle:
            return "/twitter/profile", {"handle": handle}

    elif "linkedin.com" in host:
        if "/company/" in path:
            return "/linkedin/company", {"url": url}
        if "/in/" in path:
            return "/linkedin/profile", {"url": url}
        if re.search(r"/(posts|pulse|feed/update)/", path):
            return "/linkedin/post", {"url": url}

    elif "reddit.com" in host:
        if "/comments/" in path:
            return "/reddit/post/comments", {"url": url}

    raise ValueError(
        f"socialcrawl has no endpoint for URL {url!r}; "
        "force one via opts.extra['socialcrawl_endpoint']"
    )


def _adapt(opts: ScrapeOptions, url: str) -> tuple[str, dict[str, str]]:
    """Resolve ``(endpoint, params)`` for ``url``, applying ``socialcrawl_*`` extras."""
    extra = strip_extra_prefix(opts.extra, "socialcrawl_")
    forced_endpoint = extra.pop("endpoint", None)

    if forced_endpoint is not None:
        params = {str(k): str(v) for k, v in extra.items()}
        if not (_TARGET_PARAMS & params.keys()):
            params["url"] = url
        return _normalize_endpoint(forced_endpoint), params

    endpoint, params = _route_for(url)
    params.update({str(k): str(v) for k, v in extra.items()})
    return endpoint, params


class SocialCrawlEngine(ScrapeEngine):
    """Scraping engine backed by the SocialCrawl REST API."""

    NAME = "socialcrawl"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
        output_native_markdown=False,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SOCIALCRAWL_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        endpoint, params = _adapt(opts, url)
        headers = {"x-api-key": self.api_key or ""}

        logger.debug("socialcrawl endpoint=%s params=%s", endpoint, params)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_BASE_URL}{endpoint}", params=params, headers=headers)

        response.raise_for_status()

        payload = response.json()
        text_out, markdown_out = json_to_scrape_text(payload)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=payload,
            meta={
                "status_code": response.status_code,
                "socialcrawl_endpoint": endpoint,
                "socialcrawl_credits_used": payload.get("credits_used"),
                "socialcrawl_credits_remaining": payload.get("credits_remaining"),
                "socialcrawl_request_id": payload.get("request_id"),
                "socialcrawl_cached": payload.get("cached"),
            },
        )


__all__ = ["SocialCrawlEngine"]
