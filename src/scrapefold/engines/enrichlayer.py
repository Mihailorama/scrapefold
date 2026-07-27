"""EnrichLayerEngine — paid REST API for LinkedIn people/company enrichment.

EnrichLayer (https://enrichlayer.com — the Proxycurl successor) is a managed
enrichment layer over LinkedIn public data: person profiles, company profiles,
school profiles and job postings, plus lookup endpoints (person by name +
company, reverse email, work email, …). Every endpoint returns **structured
JSON natively**, so this engine fills ``ScrapeResult.json`` and post-converts
the payload to ``text``/``markdown`` (it never produces HTML).

There is no official Python SDK — this is a pure REST adapter over
``httpx.AsyncClient``.

Pinned API contract (as of 2026-07):
  Method   : GET
  Base URL : https://enrichlayer.com
  Auth     : Authorization: Bearer <api_key>  (request header)
  Response : JSON object, shape varies per endpoint (Proxycurl-compatible).
  Billing  : credits per successful request (1 credit ≈ $0.02 pay-as-you-go);
             profile/company/school cost 1 credit, job costs 2.

URL → endpoint routing
----------------------
This is a *site-classified* engine: the target URL is mapped to the matching
EnrichLayer endpoint and query parameter. The table below is the native
parameter surface honored by this engine.

============================  ==========================  ========================
Target URL                     Endpoint                    Query param
============================  ==========================  ========================
linkedin.com/in/<id>           /api/v2/profile             profile_url=<full url>
linkedin.com/company/<id>      /api/v2/company             url=<full url>
linkedin.com/showcase/<id>     /api/v2/company             url=<full url>
linkedin.com/school/<id>       /api/v2/school              url=<full url>
linkedin.com/jobs/view/<id>    /api/v2/job                 url=<full url>
(twitter|x).com/<handle>       /api/v2/profile             twitter_profile_url=…
facebook.com/<handle>          /api/v2/profile             facebook_profile_url=…
============================  ==========================  ========================

Escape hatch
------------
Set ``opts.extra["enrichlayer_endpoint"]`` to force a specific endpoint path
(e.g. ``"/api/v2/profile/resolve"`` for the person-lookup endpoint). Any other
``enrichlayer_*`` extra is stripped of its prefix and merged into the query
string — e.g. ``extra={"enrichlayer_use_cache": "if-present"}`` adds
``?use_cache=if-present``. When an endpoint is forced, ``url`` is added
automatically only if no target param (``url``/``profile_url``/
``twitter_profile_url``/``facebook_profile_url``/``company_domain``/…) is
supplied via extras.

A URL that matches no known EnrichLayer endpoint raises ``ValueError``
(wrapped as ``EngineError`` by the base class) so the router can escalate to
another engine.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import normalize_social

logger = logging.getLogger(__name__)

_BASE_URL = "https://enrichlayer.com"
_CREDIT_USD = 0.02  # pay-as-you-go mid-tier; subscriptions go down to ~$0.008

# Endpoint path -> credits billed per successful request (default 1).
_ENDPOINT_CREDITS = {
    "/api/v2/profile": 1,
    "/api/v2/company": 1,
    "/api/v2/school": 1,
    "/api/v2/job": 2,
    "/api/v2/profile/resolve": 2,
    "/api/v2/credit-balance": 0,
}

# Query-parameter names that carry the lookup target. When an endpoint is
# forced via opts.extra and one of these is already provided, we do NOT
# auto-add ``url``.
_TARGET_PARAMS = frozenset(
    {
        "url",
        "profile_url",
        "twitter_profile_url",
        "facebook_profile_url",
        "company_domain",
        "first_name",
        "email",
        "phone_number",
        "role",
        "company_name",
    }
)


def _host(url: str) -> str:
    """Return the lowercased host of ``url`` without a leading ``www.``."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _path(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").path


def _first_segment(path: str) -> str | None:
    """Return the first non-empty path segment, or ``None`` for a bare host."""
    for seg in path.split("/"):
        if seg:
            return seg
    return None


def _route_for(url: str) -> tuple[str, dict[str, str]]:
    """Map a target URL to ``(endpoint_path, query_params)``.

    Raises ``ValueError`` when the URL matches no known EnrichLayer endpoint.
    """
    host = _host(url)
    path = _path(url)

    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        if "/in/" in path:
            return "/api/v2/profile", {"profile_url": url}
        if "/company/" in path or "/showcase/" in path:
            return "/api/v2/company", {"url": url}
        if "/school/" in path:
            return "/api/v2/school", {"url": url}
        if "/jobs/view/" in path:
            return "/api/v2/job", {"url": url}

    elif host in ("twitter.com", "x.com") or host.endswith((".twitter.com", ".x.com")):
        if "/status/" not in path and _first_segment(path):
            return "/api/v2/profile", {"twitter_profile_url": url}

    elif host == "facebook.com" or host.endswith(".facebook.com"):
        if _first_segment(path):
            return "/api/v2/profile", {"facebook_profile_url": url}

    raise ValueError(
        f"enrichlayer has no endpoint for URL {url!r}; "
        "force one via opts.extra['enrichlayer_endpoint']"
    )


def _adapt(opts: ScrapeOptions, url: str) -> tuple[str, dict[str, str]]:
    """Resolve ``(endpoint, params)`` for ``url``, applying ``enrichlayer_*`` extras.

    ``enrichlayer_endpoint`` forces the endpoint path; every other
    ``enrichlayer_*`` extra becomes a query parameter (prefix stripped).
    """
    extra = strip_extra_prefix(opts.extra, "enrichlayer_")
    forced_endpoint = extra.pop("endpoint", None)

    if forced_endpoint is not None:
        params: dict[str, str] = {str(k): str(v) for k, v in extra.items()}
        if not (_TARGET_PARAMS & params.keys()):
            params["url"] = url
        return str(forced_endpoint), params

    endpoint, params = _route_for(url)
    params.update({str(k): str(v) for k, v in extra.items()})
    return endpoint, params


class EnrichLayerEngine(ScrapeEngine):
    """Scraping engine backed by the EnrichLayer REST API.

    Specialised for LinkedIn public data — maps the target URL to the matching
    ``/api/v2/<resource>`` endpoint and returns the JSON payload as structured
    ``ScrapeResult.json``. Person profiles can also be resolved from Twitter/X
    and Facebook profile URLs.

    API key is read from the constructor argument or the
    ``ENRICHLAYER_API_KEY`` environment variable. ``is_available()`` returns
    ``False`` when neither is set.
    """

    NAME = "enrichlayer"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=_CREDIT_USD,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
        output_native_markdown=False,
        avg_response_mb_estimate=0.5,  # JSON payload
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("ENRICHLAYER_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* via the EnrichLayer API and return a ``ScrapeResult``."""
        endpoint, params = _adapt(opts, url)
        headers = {"Authorization": f"Bearer {self.api_key or ''}"}

        logger.debug("enrichlayer endpoint=%s params=%s", endpoint, params)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(f"{_BASE_URL}{endpoint}", params=params, headers=headers)

        # Surface upstream failures (401 bad key, 403 out of credits, 404 not
        # found, 429 rate limit, 5xx) so the router can distinguish "no data"
        # from "API error" and escalate.
        response.raise_for_status()

        payload = response.json()
        text_out, markdown_out = json_to_scrape_text(payload)

        # Person, company and school payloads are all profile-shaped.
        social = (
            normalize_social(payload, platform="linkedin", kind="profile")
            if endpoint in ("/api/v2/profile", "/api/v2/company", "/api/v2/school")
            else None
        )

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=_ENDPOINT_CREDITS.get(endpoint, 1) * _CREDIT_USD,
            json=payload,
            social=social,
            meta={
                "status_code": response.status_code,
                "enrichlayer_endpoint": endpoint,
            },
        )


__all__ = ["EnrichLayerEngine"]
