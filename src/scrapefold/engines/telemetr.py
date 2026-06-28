"""TelemetrEngine — Telegram channel analytics (telemetr.io).

.. warning::

   **UNVERIFIED CONTRACT.** Telemetr's API docs sit behind authentication, so
   the endpoint paths and response shape below are a *best-effort* default, not
   a pinned/verified contract. They are fully overridable: set
   ``opts.extra["telemetr_endpoint"]`` (and ``telemetr_*`` query extras) once
   you have a real API key and the actual contract. The adapter mechanics
   (auth, envelope unwrap, normalization into ``ScrapeResult.social``) are
   sound regardless; only the default routing/field names need confirming.

Pure REST over ``httpx.AsyncClient``.

Assumed contract (best-effort):
  Method   : GET
  Base URL : https://api.telemetr.io/v1   (override via TELEMETR_BASE_URL)
  Auth     : Authorization: Bearer <api_key>
  Envelope : {"data": {...}}  or a bare object/list

URL -> endpoint routing (best-effort)
-------------------------------------
============================  ==============================  ==========
Target URL                     Endpoint                        kind
============================  ==============================  ==========
t.me/<channel>                 /channels/@<channel>            profile
t.me/s/<channel>               /channels/@<channel>/posts      post
t.me/<channel>/<id>            /posts/<id>                     post
============================  ==============================  ==========
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

_DEFAULT_BASE_URL = "https://api.telemetr.io/v1"
_COST_PER_CALL = 0.002
_TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog")


def _base_url() -> str:
    return os.getenv("TELEMETR_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _is_telegram(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _TELEGRAM_HOSTS)


def _route_for(url: str) -> tuple[str, str]:
    """Best-effort map of a t.me URL to ``(endpoint, kind)``."""
    if not _is_telegram(url):
        raise ValueError(f"telemetr has no default endpoint for non-Telegram URL {url!r}")
    segments = [s for s in urlparse(url if "://" in url else f"https://{url}").path.split("/") if s]
    if not segments:
        raise ValueError(f"telemetr: no channel in URL {url!r}")
    if segments[0] == "s":
        return f"/channels/@{segments[1]}/posts", "post"
    if len(segments) >= 2 and re.fullmatch(r"\d+", segments[1]):
        return f"/posts/{segments[1]}", "post"
    return f"/channels/@{segments[0]}", "profile"


def _adapt(opts: ScrapeOptions, url: str) -> tuple[str, dict[str, str], str | None]:
    extra = strip_extra_prefix(opts.extra, "telemetr_")
    forced = extra.pop("endpoint", None)
    params = {str(k): str(v) for k, v in extra.items()}
    if forced is not None:
        return str(forced), params, None
    endpoint, kind = _route_for(url)
    return endpoint, params, kind


def _unwrap(payload: object) -> object:
    if isinstance(payload, dict):
        for key in ("data", "items", "posts", "result"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                return value
    return payload


class TelemetrEngine(ScrapeEngine):
    """Telemetr.io analytics engine (best-effort contract, override-friendly)."""

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

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        endpoint, params, kind = _adapt(opts, url)
        headers = {"Authorization": f"Bearer {self.api_key or ''}"}

        logger.debug("telemetr endpoint=%s params=%s", endpoint, params)

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
            social=normalize_social(entity, platform="telegram", kind=kind),  # type: ignore[arg-type]
            meta={"status_code": response.status_code, "telemetr_endpoint": endpoint},
        )


__all__ = ["TelemetrEngine"]
