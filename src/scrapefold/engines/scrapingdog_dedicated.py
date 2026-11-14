"""ScrapingdogDedicatedEngine — Scrapingdog's site-specific JSON endpoints.

The generic ``/scrape`` endpoint lives in ``scrapingdog.py``. This engine covers
the dedicated endpoints that return **structured JSON**: LinkedIn profiles and
companies, LinkedIn jobs, Amazon products and Twitter/X. The payload is kept in
``ScrapeResult.json``; ``text``/``markdown`` are its pretty-printed rendering.
No HTML is produced.

The request parameters are copied from ai-utils
(``services/url_to_text_service.py::get_content_scrapingdog``), which routes
these URL classes here by default (AIDE-164 parity, SCRAPEFOLD-20).

URL -> endpoint routing (native parameter surface)
--------------------------------------------------

============================  =======================  ===========================================
Target URL                     Endpoint                 Query params (besides ``api_key``)
============================  =======================  ===========================================
linkedin.com/in/<id>           /linkedin                type=profile, linkId=<id>, private=true
linkedin.com/company/<id>      /linkedin                type=company, linkId=<id>
linkedin.com/jobs/view/<id>    /linkedinjobs            job_id=<id> (or ``currentJobId`` query)
amazon.<tld>/dp/<asin>         /amazon/product          domain=<tld>, asin=<asin>
(twitter|x).com/...            /twitter                 url=<full url>, parsed=true
============================  =======================  ===========================================

How ids are read:

- ``linkId`` is the path segment after ``/in/`` or ``/company/``, percent-
  decoded; ``asin`` is the segment after ``/dp/`` or ``/gp/product/``. A
  trailing slash, a title slug or a ``/ref=...`` suffix does not change them.
- the job id is the trailing digit run of the path, or the ``currentJobId``
  query parameter.
- every endpoint is called over ``https``; ``x.com`` URLs go to ``/twitter``
  as well as ``twitter.com``.

Escape hatch: every ``opts.extra["scrapingdog_dedicated_*"]`` key is added to
the query string with the prefix stripped (and overrides the defaults above).

``probe()`` returns ``False`` for a URL that matches no endpoint, so the router
skips the engine without a call; calling ``scrape()`` directly on such a URL
raises ``ValueError`` (wrapped as ``EngineError``). Non-200 responses and 200
responses that are empty or error-shaped (``{"success": false, ...}``) raise
too, so the ladder falls back.
"""

from __future__ import annotations

import logging
import os
import re
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ProbeScope, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import Kind, normalize_social

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.scrapingdog.com"
# Placeholder per-call estimate: dedicated endpoints cost more credits than
# /scrape. Verify against Scrapingdog's current pricing page.
_COST_USD = 0.005

_LINKEDIN_ID = re.compile(r"linkedin\.com/(in|company)/([^/?#]+)")
_JOB_ID = re.compile(r"(\d+)/?$")
# Keys an error body carries; a dict with nothing else holds no data.
_ERROR_KEYS = frozenset({"success", "error", "errors", "message", "status", "code", "detail"})
_AMAZON = re.compile(r"amazon\.([a-z.]+)/(?:.*/)?(?:dp|gp/product)/([A-Za-z0-9]+)")


def _route_for(url: str) -> tuple[str, dict[str, str], str | None]:
    """Map ``url`` to ``(endpoint, params, social_platform)``.

    Raises ``ValueError`` when no dedicated endpoint fits.
    """
    host = urlparse(url).netloc.lower().removeprefix("www.")

    m = _LINKEDIN_ID.search(url)
    if m:
        kind, raw_id = m.groups()
        # httpx encodes query values itself; pass the decoded id.
        link_id = unquote(raw_id)
        if kind == "in":
            params = {"type": "profile", "linkId": link_id, "private": "true"}
        else:
            params = {"type": "company", "linkId": link_id}
        return "/linkedin", params, "linkedin"

    if "linkedin.com/jobs/" in url:
        parsed = urlparse(url)
        job_id = parse_qs(parsed.query).get("currentJobId", [""])[0]
        if not job_id:
            # /jobs/view/<id>/ or /jobs/view/<slug>-<id>/: the trailing digit run.
            m = _JOB_ID.search(parsed.path)
            job_id = m.group(1) if m else ""
        if job_id:
            return "/linkedinjobs", {"job_id": job_id}, None

    m = _AMAZON.search(url)
    if m:
        domain, asin = m.groups()
        return "/amazon/product", {"domain": domain, "asin": asin}, None

    if host in ("twitter.com", "x.com", "mobile.twitter.com", "mobile.x.com"):
        return "/twitter", {"url": url, "parsed": "true"}, "twitter"

    raise ValueError(f"scrapingdog_dedicated has no endpoint for URL {url!r}")


def _is_error_payload(payload: object) -> bool:
    """True for an empty payload or an error-shaped dict (no data to return)."""
    if not payload:
        return True
    if isinstance(payload, dict):
        return payload.get("success") is False or set(payload) <= _ERROR_KEYS
    return False


class ScrapingdogDedicatedEngine(ScrapeEngine):
    """Scrapingdog LinkedIn / Amazon / Twitter JSON endpoints.

    API key from the constructor or ``SCRAPINGDOG_API_KEY`` (shared with the
    generic ``scrapingdog`` engine).
    """

    NAME = "scrapingdog_dedicated"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        estimated_cost_usd=_COST_USD,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        site_classified=True,
        default_timeout_s=60,
        avg_response_mb_estimate=0.5,  # JSON payload
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"timeout_s", "extra"})
    # A URL without a dedicated endpoint is "not applicable": the router skips
    # the engine without billing it or using an engine slot.
    PROBE_SCOPE: ClassVar[ProbeScope] = "per_url"

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPINGDOG_API_KEY"))

    async def probe(self, url: str) -> bool:
        try:
            _route_for(url)
        except ValueError:
            return False
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        endpoint, route_params, platform = _route_for(url)
        params = {"api_key": self.api_key or "", **route_params}
        params.update(
            {k: str(v) for k, v in strip_extra_prefix(opts.extra, "scrapingdog_dedicated_").items()}
        )
        logger.debug("scrapingdog_dedicated endpoint=%s url=%s", endpoint, url)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_BASE_URL}{endpoint}", params=params)

        # Any non-200 is a failure; surface it so the router escalates.
        if response.status_code != 200:
            raise ValueError(f"{endpoint} returned HTTP {response.status_code}")

        payload = response.json()
        # A 200 without data would count as a success (json is not None) and
        # stop the ladder; fail instead so the next step runs.
        if _is_error_payload(payload):
            raise ValueError(f"{endpoint} returned no data: {str(payload)[:200]}")
        text, markdown = json_to_scrape_text(payload)
        kind: Kind | None = "profile" if platform == "linkedin" else None
        social = normalize_social(payload, platform=platform, kind=kind) if platform else None

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=None,
            json=payload,
            social=social,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=_COST_USD,
            meta={"status_code": response.status_code, "scrapingdog_endpoint": endpoint},
        )


__all__ = ["ScrapingdogDedicatedEngine"]
