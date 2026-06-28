"""LabelUpEngine — influencer-marketing / Telegram analytics gateway (labelup).

.. warning::

   **UNVERIFIED CONTRACT — gateway only.** LabelUp's API is not publicly
   documented, so this engine ships *no default URL routing*. It is a thin
   authenticated JSON gateway: you supply the endpoint explicitly via
   ``opts.extra["labelup_endpoint"]`` (and ``labelup_*`` query extras), it
   GETs ``<base><endpoint>`` with bearer auth, stores the JSON, and normalizes
   it into ``ScrapeResult.social``. Set the base via ``LABELUP_BASE_URL`` and
   the kind hint via ``opts.extra["labelup_kind"]`` (``profile``/``post``).

Once the real contract is known, this can grow URL->endpoint routing like the
``tgstat`` engine. Until then the gateway keeps it usable and honest.
"""

from __future__ import annotations

import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import Kind, normalize_social

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.labelup.ru/v1"
_COST_PER_CALL = 0.002
_VALID_KINDS = ("profile", "post", "comment")


def _base_url() -> str:
    return os.getenv("LABELUP_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


def _unwrap(payload: object) -> object:
    if isinstance(payload, dict):
        for key in ("data", "items", "result", "response"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                return value
    return payload


class LabelUpEngine(ScrapeEngine):
    """LabelUp authenticated JSON gateway (no default routing — endpoint required)."""

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
        extra = strip_extra_prefix(opts.extra, "labelup_")
        endpoint = extra.pop("endpoint", None)
        if not endpoint:
            raise ValueError(
                "labelup is gateway-only: set opts.extra['labelup_endpoint'] "
                "(no default URL routing until the API contract is confirmed)"
            )

        kind_hint = extra.pop("kind", None)
        kind: Kind | None = kind_hint if kind_hint in _VALID_KINDS else None
        params = {str(k): str(v) for k, v in extra.items()}
        headers = {"Authorization": f"Bearer {self.api_key or ''}"}

        logger.debug("labelup endpoint=%s params=%s", endpoint, params)

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
            meta={"status_code": response.status_code, "labelup_endpoint": str(endpoint)},
        )


__all__ = ["LabelUpEngine"]
