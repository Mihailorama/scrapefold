"""OxylabsEngine — Oxylabs Web Scraper API via the realtime (synchronous) endpoint.

Pure REST, no SDK. POSTs a job to ``https://realtime.oxylabs.io/v1/queries`` and
gets the scraped page back in the same response (no polling). Authentication is
HTTP Basic with the dashboard ``USERNAME`` / ``PASSWORD`` pair.

Uses the generic ``universal`` source so any URL works; JS rendering, geo
routing, custom headers, and cookies are supported. Specialized Oxylabs sources
(``amazon_product``, ``google_search``, …) are reachable by overriding the
source via ``extra["oxylabs_source"]`` but are otherwise out of scope here.

Native parameter surface (universal source)
-------------------------------------------
| Native payload key   | Maps from (unified)        | Notes                              |
|----------------------|----------------------------|------------------------------------|
| ``source``           | — (const ``"universal"``)  | override via ``extra["oxylabs_source"]`` |
| ``url``              | ``url``                    | target URL                         |
| ``render``           | ``render_js`` / ``take_screenshot`` | ``"html"`` for JS, ``"png"`` for a screenshot |
| ``geo_location``     | ``country``                | location string; override via ``extra`` |
| ``context[headers]`` | ``language``/``user_agent``/``custom_headers``/``cookies`` | forwarded request headers |
| ``parse``            | ``extra["oxylabs_parse"]`` | structured parsing (dedicated sources / instructions) |
| ``oxylabs_*``        | ``extra``                  | any other key forwarded verbatim   |
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://realtime.oxylabs.io/v1/queries"


def _adapt(opts: ScrapeOptions, url: str) -> dict[str, Any]:
    """Map unified ``ScrapeOptions`` to an Oxylabs realtime JSON payload."""
    payload: dict[str, Any] = {"source": "universal", "url": url}

    # render: "html" enables the headless browser; "png" returns a screenshot.
    # take_screenshot wins over plain JS rendering.
    if opts.take_screenshot:
        payload["render"] = "png"
    elif opts.render_js:
        payload["render"] = "html"

    if opts.country:
        payload["geo_location"] = opts.country

    # Localization / UA / custom headers / cookies all travel as forwarded
    # request headers inside the universal source's "context".
    headers = build_target_headers(opts)
    if headers:
        payload["context"] = [{"key": "headers", "value": headers}]

    # extra["oxylabs_*"] keys are forwarded verbatim as top-level payload keys
    # (e.g. oxylabs_source, oxylabs_user_agent_type, oxylabs_parse,
    # oxylabs_parsing_instructions, oxylabs_geo_location).
    payload.update(strip_extra_prefix(opts.extra, "oxylabs_"))
    return payload


class OxylabsEngine(ScrapeEngine):
    """Oxylabs Web Scraper API engine (realtime/synchronous integration).

    Credentials are read from the constructor or the ``OXYLABS_USERNAME`` /
    ``OXYLABS_PASSWORD`` environment variables. ``is_available()`` returns
    ``False`` unless both are present. No optional extra is required — the
    engine is pure ``httpx`` REST.
    """

    NAME = "oxylabs"
    # Per-result placeholder; Web Scraper API bills per successful result and
    # the rate varies by plan (rendered requests cost more). Verify against the
    # current Oxylabs pricing page before relying on the router cost budget.
    _PER_RESULT_USD = 0.0028
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        estimated_cost_usd=_PER_RESULT_USD,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="residential",
        free_tier=False,
        default_timeout_s=60,
        avg_response_mb_estimate=3.0,  # rendered-HTML proxy API response
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "user_agent",
            "custom_headers",
            "cookies",
            "take_screenshot",
            "output_format",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, username: str | None = None, password: str | None = None) -> None:
        username = username or os.getenv("OXYLABS_USERNAME")
        password = password or os.getenv("OXYLABS_PASSWORD")
        super().__init__(api_key=password)
        self.username = username
        self.password = password

    def is_available(self) -> bool:
        return bool(self.username) and bool(self.password)

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        payload = _adapt(opts, url)
        auth = httpx.BasicAuth(self.username or "", self.password or "")

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.post(_ENDPOINT, json=payload, auth=auth)

        # Non-2xx from Oxylabs itself = the scrape job failed (auth, quota, bad
        # request). Surface the API's message so the router can escalate.
        if response.status_code >= 400:
            raise EngineError(
                self.NAME,
                f"Oxylabs API {response.status_code}: {_error_message(response)}",
                0,  # elapsed_ms filled by base class
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise EngineError(self.NAME, f"non-JSON response from Oxylabs: {exc}", 0) from exc

        results = body.get("results") if isinstance(body, dict) else None
        if not results:
            raise EngineError(self.NAME, "Oxylabs returned no results", 0)

        first = results[0]
        content = first.get("content")
        meta: dict[str, Any] = {"status_code": first.get("status_code")}
        job_id = first.get("job_id") or (body.get("job") or {}).get("id")
        if job_id is not None:
            meta["oxylabs_job_id"] = job_id

        # Screenshot mode: content is a base64-encoded PNG string.
        if opts.take_screenshot:
            return ScrapeResult(
                url=url,
                text="",
                markdown="",
                html=None,
                engine=self.NAME,
                elapsed_ms=0,
                cost_usd=self._PER_RESULT_USD,
                screenshot_b64=content if isinstance(content, str) else None,
                meta=meta,
            )

        # Parsed (structured) content comes back as a dict/list.
        if isinstance(content, (dict, list)):
            text = str(content)
            return ScrapeResult(
                url=url,
                text=text,
                markdown=text,
                html=None,
                json=content,
                engine=self.NAME,
                elapsed_ms=0,
                cost_usd=self._PER_RESULT_USD,
                meta=meta,
            )

        # Default: content is the page HTML.
        html_body = content if isinstance(content, str) else ""
        text, markdown = html_to_both(html_body, base_url=url)
        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html_body,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=self._PER_RESULT_USD,
            meta=meta,
        )


def _error_message(response: httpx.Response) -> str:
    """Best-effort extraction of an error message from an Oxylabs response."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        msg = body.get("message") or body.get("error")
        if msg:
            return str(msg)
    return str(body)[:200]


__all__ = ["OxylabsEngine", "_adapt"]
