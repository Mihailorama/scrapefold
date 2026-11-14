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

Differences from ai-utils, on purpose:

- ids are read from the path segment after ``/in/``, ``/company/``, ``/dp/``
  (or ``/gp/product/``), so a trailing slash or a ``/ref=...`` suffix no longer
  yields an empty or wrong ``linkId``/``asin`` (ai-utils takes the last path
  segment). The job id is the trailing digit run of the path, or the
  ``currentJobId`` query parameter.
- every endpoint is called over ``https``.
- ``x.com`` URLs are routed to ``/twitter`` too (ai-utils matches only
  ``twitter.com/``).

Escape hatch: every ``opts.extra["scrapingdog_dedicated_*"]`` key is added to
the query string with the prefix stripped (and overrides the defaults above).

A URL that matches no endpoint raises ``ValueError`` (wrapped as
``EngineError``), so the router escalates. Non-200 responses raise too.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import parse_qs, urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
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
_AMAZON = re.compile(r"amazon\.([a-z.]+)/(?:.*/)?(?:dp|gp/product)/([A-Za-z0-9]+)")


def _route_for(url: str) -> tuple[str, dict[str, str], str | None]:
    """Map ``url`` to ``(endpoint, params, social_platform)``.

    Raises ``ValueError`` when no dedicated endpoint fits.
    """
    host = urlparse(url).netloc.lower().removeprefix("www.")

    m = _LINKEDIN_ID.search(url)
    if m:
        kind, link_id = m.groups()
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

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPINGDOG_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        endpoint, route_params, platform = _route_for(url)
        params = {"api_key": self.api_key or "", **route_params}
        params.update(
            {k: str(v) for k, v in strip_extra_prefix(opts.extra, "scrapingdog_dedicated_").items()}
        )
        logger.debug("scrapingdog_dedicated endpoint=%s url=%s", endpoint, url)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_BASE_URL}{endpoint}", params=params)

        # ai-utils treats any non-200 as a failure; surface it so the router escalates.
        if response.status_code != 200:
            raise ValueError(f"{endpoint} returned HTTP {response.status_code}")

        payload = response.json()
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
