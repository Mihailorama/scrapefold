"""LabelUpEngine — multi-platform influencer / social analytics API (labelup.ru).

LabelUp (LUP) spans **many** social networks and messengers — Instagram,
TikTok, VK, YouTube, Telegram, RUTUBE, and Dzen — not just Telegram. The
normalized ``platform`` is inferred from the target URL's host (or set
explicitly via ``opts.extra["labelup_platform"]``), never hard-coded.

Pure REST over ``httpx.AsyncClient`` — there is no official Python SDK.

Pinned API contract (verified 2026-06, https://help.labelup.ru/article/23384):
  Method   : GET
  Base URL : https://labelup.ru/api/v2
  Endpoint : /accounts/statistics
  Auth     : Authorization: Bearer <api_key>  +  X-Requested-With: XMLHttpRequest
  Billing  : per blogger report; API unlocks with a reports-enabled plan.

The single statistics endpoint accepts one of four parameter shapes:
  * ``url=<profile url>``                 (the universal route used by default)
  * ``id=<labelup account id>``
  * ``network_id=<n>&nickname=<handle>``
  * ``network_id=<n>&uid=<platform uid>``

``network_id`` maps a platform to LabelUp's numeric id:
  1 Instagram · 2 YouTube · 3 VK · 4 Telegram · 7 TikTok · 8 RUTUBE · 9 Dzen

The response is a single account object (profile + stats + recent posts), so
the normalized entity defaults to a ``Profile``.

URL -> endpoint routing
-----------------------
``scrape(url)`` GETs ``/accounts/statistics?url=<url>`` — works for every
supported platform, since LabelUp resolves the profile from the raw URL.

Escape hatches (``labelup_*`` extras)
-------------------------------------
  * ``labelup_endpoint``  — force a different path (raw gateway mode).
  * ``labelup_nickname`` / ``labelup_uid`` / ``labelup_id`` / ``labelup_network_id``
    — use handle/uid/id lookup instead of the ``url`` route (``network_id`` is
    derived from the inferred platform when omitted).
  * ``labelup_platform`` — override host-based platform inference.
  * ``labelup_kind``     — override the entity kind hint (``profile``/``post``).
  * any other ``labelup_*`` — stripped of its prefix and merged into the query.
"""

from __future__ import annotations

import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import Kind, normalize_social, platform_for_url

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://labelup.ru/api/v2"
_STATISTICS_ENDPOINT = "/accounts/statistics"
_COST_PER_CALL = 0.002
_VALID_KINDS = ("profile", "post", "comment")

# Platform -> LabelUp numeric network id (for nickname/uid lookups).
NETWORK_IDS: dict[str, int] = {
    "instagram": 1,
    "youtube": 2,
    "vk": 3,
    "telegram": 4,
    "tiktok": 7,
    "rutube": 8,
    "dzen": 9,
}

# Param shapes that already pin the account, so we must NOT also send ``url``.
_ROUTING_KEYS = frozenset({"network_id", "nickname", "uid", "id", "url"})


def _base_url() -> str:
    return os.getenv("LABELUP_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _adapt(opts: ScrapeOptions, url: str) -> tuple[str, dict[str, str], Kind | None, str | None]:
    """Resolve ``(endpoint, params, kind, platform)`` from the URL + ``labelup_*`` extras.

    Default route is ``/accounts/statistics?url=<url>``. A forced
    ``labelup_endpoint`` switches to raw gateway mode; explicit
    ``nickname``/``uid``/``id`` extras switch to handle/id lookup.
    """
    extra = strip_extra_prefix(opts.extra, "labelup_")
    forced_endpoint = extra.pop("endpoint", None)
    kind_hint = extra.pop("kind", None)
    kind: Kind | None = kind_hint if kind_hint in _VALID_KINDS else None
    # Multi-platform: tag by explicit override, else infer from the URL host.
    platform = extra.pop("platform", None) or platform_for_url(url)

    params = {str(k): str(v) for k, v in extra.items()}

    if forced_endpoint is not None:
        return str(forced_endpoint), params, kind, platform

    # Verified default route. Fall back to the universal ``url`` param unless the
    # caller pinned the account some other way (nickname/uid/id/network_id).
    if not (_ROUTING_KEYS & params.keys()):
        params["url"] = url
    # A handle/uid lookup needs a network_id; derive it from the platform.
    if ("nickname" in params or "uid" in params) and "network_id" not in params:
        net = NETWORK_IDS.get(platform or "")
        if net is not None:
            params["network_id"] = str(net)
    # /accounts/statistics returns a profile-with-stats entity.
    if kind is None:
        kind = "profile"
    return _STATISTICS_ENDPOINT, params, kind, platform


def _unwrap(payload: object) -> object:
    if isinstance(payload, dict):
        for key in ("data", "items", "result", "response"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                return value
    return payload


class LabelUpEngine(ScrapeEngine):
    """Multi-platform analytics engine backed by the LabelUp REST API.

    Maps a public profile URL to ``/accounts/statistics``, returns the JSON in
    ``ScrapeResult.json``, and normalizes the account into ``ScrapeResult.social``
    (``platform`` inferred from the URL host).

    API key from the constructor or ``LABELUP_API_TOKEN``. ``is_available()``
    returns ``False`` when neither is set.
    """

    NAME = "labelup"
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
        super().__init__(api_key or os.getenv("LABELUP_API_TOKEN"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        endpoint, params, kind, platform = _adapt(opts, url)
        headers = {
            "Authorization": f"Bearer {self.api_key or ''}",
            "X-Requested-With": "XMLHttpRequest",
        }

        logger.debug("labelup endpoint=%s params=%s platform=%s", endpoint, params, platform)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_base_url()}{endpoint}", params=params, headers=headers)
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
            social=normalize_social(entity, platform=platform, kind=kind),  # type: ignore[arg-type]
            meta={
                "status_code": response.status_code,
                "labelup_endpoint": str(endpoint),
                "labelup_platform": platform,
            },
        )


__all__ = ["NETWORK_IDS", "LabelUpEngine"]
