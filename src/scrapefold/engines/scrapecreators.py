"""ScrapeCreatorsEngine — paid REST API for social-media public data.

Scrape Creators (https://app.scrapecreators.com) exposes 110+ REST endpoints
across 33+ platforms (TikTok, Instagram, YouTube, Twitter/X, Reddit, …). Every
endpoint returns **structured JSON natively**, so this engine fills
``ScrapeResult.json`` and post-converts the payload to ``text``/``markdown``
(it never produces HTML).

There is no official Python SDK — this is a pure REST adapter over
``httpx.AsyncClient``.

Pinned API contract (as of 2026-06):
  Method   : GET
  Base URL : https://api.scrapecreators.com
  Auth     : x-api-key: <api_key>  (request header)
  Response : JSON object, shape varies per endpoint.
  Billing  : 1 credit per request.

URL → endpoint routing
----------------------
This is a *site-classified* engine: the target URL is mapped to the matching
Scrape Creators endpoint and query parameter. The table below is the native
parameter surface honored by this engine.

============================  =========================  ==================
Target URL                     Endpoint                   Query param
============================  =========================  ==================
tiktok.com/@u/video/123        /v1/tiktok/video           url=<full url>
tiktok.com/@u                   /v1/tiktok/profile         handle=<u>
instagram.com/p|reel|tv/…       /v1/instagram/post         url=<full url>
instagram.com/<u>               /v1/instagram/profile      handle=<u>
youtube.com/watch|shorts,       /v1/youtube/video          url=<full url>
  youtu.be/…
youtube.com/@h|/channel/…       /v1/youtube/channel        url=<full url>
(twitter|x).com/u/status/123    /v1/twitter/tweet          url=<full url>
(twitter|x).com/<u>             /v1/twitter/profile        handle=<u>
reddit.com/.../comments/…       /v1/reddit/post            url=<full url>
============================  =========================  ==================

Escape hatch
------------
Set ``opts.extra["scrapecreators_endpoint"]`` to force a specific endpoint
path (e.g. ``"/v1/youtube/channel"``). Any other ``scrapecreators_*`` extra is
stripped of its prefix and merged into the query string — e.g.
``extra={"scrapecreators_trim": "true"}`` adds ``?trim=true``. When an endpoint
is forced, ``url`` is added automatically only if no target param
(``url``/``handle``/``user_id``/``channelId``) is supplied via extras.

A URL that matches no known platform raises ``ValueError`` (wrapped as
``EngineError`` by the base class) so the router can escalate to another engine.
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
from scrapefold.social import normalize_social, platform_kind

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.scrapecreators.com"
_COST_PER_CALL = 0.002  # ~1 credit; coarse per-call USD estimate

# Query-parameter names that carry the scrape target. When an endpoint is forced
# via opts.extra and one of these is already provided, we do NOT auto-add ``url``.
_TARGET_PARAMS = frozenset({"url", "handle", "user_id", "channelId"})


def _host(url: str) -> str:
    """Return the lowercased host of ``url`` without a leading ``www.``."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _tiktok_handle(path: str) -> str | None:
    m = re.search(r"@([\w.\-]+)", path)
    return m.group(1) if m else None


def _first_segment(path: str) -> str | None:
    """Return the first non-empty path segment, or ``None`` for a bare host."""
    for seg in path.split("/"):
        if seg:
            return seg
    return None


def _route_for(url: str) -> tuple[str, dict[str, str]]:
    """Map a target URL to ``(endpoint_path, query_params)``.

    Raises ``ValueError`` when the URL matches no known Scrape Creators platform.
    """
    host = _host(url)
    path = urlparse(url if "://" in url else f"https://{url}").path

    if "tiktok.com" in host:
        if "/video/" in path:
            return "/v1/tiktok/video", {"url": url}
        handle = _tiktok_handle(path)
        if handle:
            return "/v1/tiktok/profile", {"handle": handle}

    elif "instagram.com" in host:
        if re.search(r"/(p|reel|reels|tv)/", path):
            return "/v1/instagram/post", {"url": url}
        handle = _first_segment(path)
        if handle:
            return "/v1/instagram/profile", {"handle": handle}

    elif "youtube.com" in host or "youtu.be" in host:
        if "youtu.be" in host or re.search(r"/(watch|shorts)\b", path) or "watch?" in url:
            return "/v1/youtube/video", {"url": url}
        # @handle, /channel/<id>, /c/<name>, /user/<name>
        return "/v1/youtube/channel", {"url": url}

    elif host in ("twitter.com", "x.com") or host.endswith((".twitter.com", ".x.com")):
        if "/status/" in path:
            return "/v1/twitter/tweet", {"url": url}
        handle = _first_segment(path)
        if handle:
            return "/v1/twitter/profile", {"handle": handle}

    elif "reddit.com" in host:
        if "/comments/" in path:
            return "/v1/reddit/post", {"url": url}

    raise ValueError(
        f"scrapecreators has no endpoint for URL {url!r}; "
        "force one via opts.extra['scrapecreators_endpoint']"
    )


def _adapt(opts: ScrapeOptions, url: str) -> tuple[str, dict[str, str]]:
    """Resolve ``(endpoint, params)`` for ``url``, applying ``scrapecreators_*`` extras.

    ``scrapecreators_endpoint`` forces the endpoint path; every other
    ``scrapecreators_*`` extra becomes a query parameter (prefix stripped).
    """
    extra = strip_extra_prefix(opts.extra, "scrapecreators_")
    forced_endpoint = extra.pop("endpoint", None)

    if forced_endpoint is not None:
        params: dict[str, str] = {str(k): str(v) for k, v in extra.items()}
        if not (_TARGET_PARAMS & params.keys()):
            params["url"] = url
        return str(forced_endpoint), params

    endpoint, params = _route_for(url)
    params.update({str(k): str(v) for k, v in extra.items()})
    return endpoint, params


class ScrapeCreatorsEngine(ScrapeEngine):
    """Scraping engine backed by the Scrape Creators REST API.

    Specialised for public social-media data — maps the target URL to the
    matching ``/v1/<platform>/<resource>`` endpoint and returns the JSON
    payload as structured ``ScrapeResult.json``.

    API key is read from the constructor argument or the
    ``SCRAPECREATORS_API_KEY`` environment variable. ``is_available()`` returns
    ``False`` when neither is set.
    """

    NAME = "scrapecreators"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
        output_native_markdown=False,
        avg_response_mb_estimate=0.5,  # JSON payload
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPECREATORS_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* via the Scrape Creators API and return a ``ScrapeResult``."""
        endpoint, params = _adapt(opts, url)
        headers = {"x-api-key": self.api_key or ""}

        logger.debug("scrapecreators endpoint=%s params=%s", endpoint, params)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_BASE_URL}{endpoint}", params=params, headers=headers)

        # Surface upstream failures (401 bad key, 402 out of credits, 5xx) so the
        # router can distinguish "no data" from "API error" and escalate.
        response.raise_for_status()

        payload = response.json()
        text_out, markdown_out = json_to_scrape_text(payload)

        platform, kind = platform_kind(endpoint)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=payload,
            social=normalize_social(payload, platform=platform, kind=kind),
            meta={
                "status_code": response.status_code,
                "scrapecreators_endpoint": endpoint,
            },
        )


__all__ = ["ScrapeCreatorsEngine"]
