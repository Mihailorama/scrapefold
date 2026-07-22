"""TGStatEngine — paid REST API for Telegram channel analytics (tgstat.ru).

TGStat (https://tgstat.ru) is the largest Telegram analytics service. Its REST
API returns structured JSON for channels and posts, so this engine fills
``ScrapeResult.json`` and normalizes it into ``ScrapeResult.social``
(``Profile`` for a channel, list of ``Post`` for a feed).

Pure REST over ``httpx.AsyncClient`` — there is no official Python SDK.

Pinned API contract (verified 2026-06, https://api.tgstat.ru/docs):
  Method   : GET
  Base URL : https://api.tgstat.ru
  Auth     : token=<api_key>  (query parameter)
  Envelope : {"status": "ok", "response": {...}}  ("error" + "error" message otherwise)
  Billing  : per request, per plan.

URL -> endpoint routing
-----------------------
============================  =========================  ==================
Target URL                     Endpoint                   Query param
============================  =========================  ==================
t.me/<channel>                 /channels/get              channelId=@<channel>
t.me/s/<channel>               /channels/posts            channelId=@<channel>
t.me/<channel>/<id>            /posts/get                 postId=<id>
============================  =========================  ==================

Escape hatch
------------
``opts.extra["tgstat_endpoint"]`` forces an endpoint path (e.g.
``"/channels/stat"``); any other ``tgstat_*`` extra is stripped of its prefix
and merged into the query string (e.g. ``extra={"tgstat_limit": 50}``).

A non-Telegram URL with no forced endpoint raises ``ValueError`` (wrapped as
``EngineError``) so the router escalates.
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
from scrapefold.social import normalize_social

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tgstat.ru"
_COST_PER_CALL = 0.002
_TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog")
_TARGET_PARAMS = frozenset({"channelId", "postId"})


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _is_telegram(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _TELEGRAM_HOSTS)


def _route_for(url: str) -> tuple[str, dict[str, str], str | None]:
    """Map a t.me URL to ``(endpoint, params, kind)``.

    ``kind`` is the normalized entity kind hint (``"profile"`` / ``"post"``).
    Raises ``ValueError`` for non-Telegram URLs.
    """
    if not _is_telegram(url):
        raise ValueError(f"tgstat has no endpoint for non-Telegram URL {url!r}")
    segments = [s for s in urlparse(url if "://" in url else f"https://{url}").path.split("/") if s]
    if not segments:
        raise ValueError(f"tgstat: no channel in URL {url!r}")
    if segments[0] == "s":
        # t.me/s/<channel> -> channel feed (posts)
        return "/channels/posts", {"channelId": f"@{segments[1]}"}, "post"
    if len(segments) >= 2 and re.fullmatch(r"\d+", segments[1]):
        # t.me/<channel>/<id> -> single post
        return "/posts/get", {"postId": segments[1]}, "post"
    # t.me/<channel> -> channel info
    return "/channels/get", {"channelId": f"@{segments[0]}"}, "profile"


def _adapt(opts: ScrapeOptions, url: str) -> tuple[str, dict[str, str], str | None]:
    """Resolve ``(endpoint, params, kind)``, applying ``tgstat_*`` extras."""
    extra = strip_extra_prefix(opts.extra, "tgstat_")
    forced_endpoint = extra.pop("endpoint", None)

    if forced_endpoint is not None:
        params = {str(k): str(v) for k, v in extra.items()}
        if not (_TARGET_PARAMS & params.keys()) and _is_telegram(url):
            params["channelId"] = url
        return str(forced_endpoint), params, None

    endpoint, params, kind = _route_for(url)
    params.update({str(k): str(v) for k, v in extra.items()})
    return endpoint, params, kind


def _unwrap(payload: object) -> object:
    """Return the ``response`` body of a TGStat envelope, else the payload itself.

    For a posts feed the entity list is ``response.items``; for channel/post it
    is ``response`` directly.
    """
    if isinstance(payload, dict) and "response" in payload:
        response = payload["response"]
        if isinstance(response, dict) and isinstance(response.get("items"), list):
            return response["items"]
        return response
    return payload


class TGStatEngine(ScrapeEngine):
    """Telegram analytics engine backed by the TGStat REST API.

    Maps a public Telegram URL to the matching channel/post endpoint, returns
    the JSON envelope in ``ScrapeResult.json``, and normalizes the entity into
    ``ScrapeResult.social`` (``platform="telegram"``).

    API key from the constructor or ``TGSTAT_API_TOKEN``. ``is_available()``
    returns ``False`` when neither is set.
    """

    NAME = "tgstat"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="none",
        site_classified=True,
        output_native_markdown=False,
        avg_response_mb_estimate=0.5,  # JSON payload
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("TGSTAT_API_TOKEN"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        endpoint, params, kind = _adapt(opts, url)
        params = {**params, "token": self.api_key or ""}

        logger.debug("tgstat endpoint=%s params=%s", endpoint, {**params, "token": "***"})

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_BASE_URL}{endpoint}", params=params)
        response.raise_for_status()

        payload = response.json()
        # TGStat signals logical failures in-band with status != "ok".
        if isinstance(payload, dict) and payload.get("status") not in (None, "ok"):
            raise RuntimeError(f"tgstat error for {url!r}: {payload.get('error', payload)}")

        entity = _unwrap(payload)
        text_out, markdown_out = json_to_scrape_text(payload)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=payload,
            social=normalize_social(entity, platform="telegram", kind=kind),  # type: ignore[arg-type]
            meta={"status_code": response.status_code, "tgstat_endpoint": endpoint},
        )


__all__ = ["TGStatEngine"]
