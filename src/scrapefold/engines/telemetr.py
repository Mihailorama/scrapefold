"""TelemetrEngine — Telegram channel analytics (telemetr.io).

Pure REST over ``httpx.AsyncClient`` — there is no official Python SDK.

Pinned API contract (verified 2026-06, https://api.telemetr.io/docs +
OpenAPI spec at https://api.telemetr.io/api-docs/openapi.json):
  Method   : GET
  Base URL : https://api.telemetr.io/v1   (override via TELEMETR_BASE_URL)
  Auth     : x-api-key: <api_key>   (key from the @telemetrio_api_bot bot)
  Billing  : per request, monthly plan quota.

Telemetr addresses channels by its own numeric ``internal_id``, **not** by the
public ``@username``. A public ``t.me`` URL is therefore resolved in two steps:

  1. ``GET /channels/search?term=<channel>&limit=1`` -> top match's internal id
  2. fetch the real resource by that ``internal_id``

URL -> endpoint routing
-----------------------
============================  ====================================  ==========
Target URL                     Endpoint (after id resolution)        kind
============================  ====================================  ==========
t.me/<channel>                 /channel/info?internal_id=<id>        profile
t.me/s/<channel>               /messages/channel?internal_id=<id>    post
t.me/<channel>/<id>            /messages/by_id?internal_id=<id>      post
                                 &message_id=<id>
============================  ====================================  ==========

Escape hatches (``telemetr_*`` extras)
--------------------------------------
  * ``telemetr_internal_id`` — skip the search step (you already know the id).
  * ``telemetr_endpoint``    — force a raw path (single call, no resolution),
    e.g. ``"/catalog/search"``; other ``telemetr_*`` extras become query params.

A non-Telegram URL with no forced endpoint raises ``ValueError`` (wrapped as
``EngineError``) so the router escalates.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import Kind, normalize_social

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.telemetr.io/v1"
_COST_PER_CALL = 0.002
_TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog")

_SEARCH_ENDPOINT = "/channels/search"
_INFO_ENDPOINT = "/channel/info"
_POSTS_ENDPOINT = "/messages/channel"
_MESSAGE_ENDPOINT = "/messages/by_id"

# Candidate keys for the channel id inside a search result item.
_ID_KEYS = ("internal_id", "id", "channel_id", "tg_id")


def _base_url() -> str:
    return os.getenv("TELEMETR_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _is_telegram(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _TELEGRAM_HOSTS)


def _route_for(url: str) -> tuple[str, str, str | None]:
    """Map a t.me URL to ``(channel_slug, target, message_id)``.

    ``target`` is one of ``"profile"`` / ``"posts"`` / ``"post"``; ``message_id``
    is set only for a single-post URL. Raises ``ValueError`` for non-Telegram
    URLs or URLs with no channel.
    """
    if not _is_telegram(url):
        raise ValueError(f"telemetr has no endpoint for non-Telegram URL {url!r}")
    segments = [s for s in urlparse(url if "://" in url else f"https://{url}").path.split("/") if s]
    if not segments:
        raise ValueError(f"telemetr: no channel in URL {url!r}")
    if segments[0] == "s":
        # t.me/s/<channel> -> channel feed
        return segments[1], "posts", None
    if len(segments) >= 2 and re.fullmatch(r"\d+", segments[1]):
        # t.me/<channel>/<id> -> single post
        return segments[0], "post", segments[1]
    # t.me/<channel> -> channel info
    return segments[0], "profile", None


def _build_request(
    target: str, internal_id: str, message_id: str | None, extra: dict[str, str]
) -> tuple[str, dict[str, str], Kind]:
    """Build ``(endpoint, params, kind)`` for a resolved ``internal_id``."""
    params: dict[str, str] = {"internal_id": internal_id}
    kind: Kind
    if target == "profile":
        endpoint, kind = _INFO_ENDPOINT, "profile"
    elif target == "post" and message_id is not None:
        endpoint, kind = _MESSAGE_ENDPOINT, "post"
        params["message_id"] = message_id
    else:  # posts feed
        endpoint, kind = _POSTS_ENDPOINT, "post"
    params.update(extra)
    return endpoint, params, kind


def _items(payload: object) -> list[Any]:
    """Return the list of result items from a search/list response."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "channels", "result", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_internal_id(item: dict[str, Any]) -> str | None:
    for key in _ID_KEYS:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _unwrap(payload: object) -> object:
    """Return the primary entity from a Telemetr response envelope."""
    if isinstance(payload, dict):
        for key in ("data", "items", "messages", "posts", "result", "channel"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                return value
    return payload


class TelemetrEngine(ScrapeEngine):
    """Telegram analytics engine backed by the Telemetr.io REST API.

    Resolves a public Telegram URL to Telemetr's ``internal_id`` via
    ``/channels/search``, fetches the channel/posts/post resource, and
    normalizes it into ``ScrapeResult.social`` (``platform="telegram"``).

    API key from the constructor or ``TELEMETR_API_TOKEN``. ``is_available()``
    returns ``False`` when neither is set.
    """

    NAME = "telemetr"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="none",
        site_classified=True,
        output_native_markdown=False,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("TELEMETR_API_TOKEN"))

    async def _resolve_internal_id(
        self, client: httpx.AsyncClient, base: str, headers: dict[str, str], slug: str
    ) -> str:
        """Resolve a public channel slug to Telemetr's numeric ``internal_id``."""
        response = await client.get(
            f"{base}{_SEARCH_ENDPOINT}", params={"term": slug, "limit": "1"}, headers=headers
        )
        response.raise_for_status()
        items = _items(response.json())
        if not items or not isinstance(items[0], dict):
            raise RuntimeError(f"telemetr: channel {slug!r} not found via search")
        internal_id = _extract_internal_id(items[0])
        if internal_id is None:
            raise RuntimeError(f"telemetr: no internal_id in search result for {slug!r}")
        return internal_id

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        extra = strip_extra_prefix(opts.extra, "telemetr_")
        forced = extra.pop("endpoint", None)
        explicit_id = extra.pop("internal_id", None)
        params = {str(k): str(v) for k, v in extra.items()}
        headers = {"x-api-key": self.api_key or ""}
        base = _base_url()
        kind: Kind | None

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            if forced is not None:
                endpoint, kind = str(forced), None
                req_params = params
            else:
                slug, target, message_id = _route_for(url)
                internal_id = (
                    str(explicit_id)
                    if explicit_id
                    else (await self._resolve_internal_id(client, base, headers, slug))
                )
                endpoint, req_params, kind = _build_request(target, internal_id, message_id, params)

            logger.debug("telemetr endpoint=%s params=%s", endpoint, req_params)
            response = await client.get(f"{base}{endpoint}", params=req_params, headers=headers)
        response.raise_for_status()

        payload = response.json()
        entity = _unwrap(payload)
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
            social=normalize_social(entity, platform="telegram", kind=kind),  # type: ignore[arg-type]
            meta={"status_code": response.status_code, "telemetr_endpoint": endpoint},
        )


__all__ = ["TelemetrEngine"]
