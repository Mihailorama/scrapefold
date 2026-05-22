"""ScrapingBee engine — paid, JS-rendering, residential proxies, screenshot support.

ScrapingBee SaaS charges per credit:
  - 1 credit ≈ $0.001 for plain HTTP
  - 5 credits for JS-rendered pages (``render_js=True``, the default)
  - 10 credits for premium proxy mode

The engine uses the ScrapingBee Python SDK (``scrapingbee>=2.0``), lazily
imported inside ``_fetch`` so that installing scrapefold without the
``scrapingbee`` extra does not raise an ``ImportError`` at import time.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)


def _get_client_cls():  # type: ignore[return]
    """Return ScrapingBeeClient, lazily importing the SDK.

    Keeping this in a separate function makes it easy to mock in tests without
    hitting the network.  Raises ``ImportError`` (not caught here) when the
    ``scrapingbee`` extra is not installed.
    """
    from scrapingbee import ScrapingBeeClient

    return ScrapingBeeClient


def _adapt(opts: ScrapeOptions) -> tuple[dict, dict]:
    """Map unified ``ScrapeOptions`` to a ``(params, headers)`` pair for the SDK.

    Returns:
        params: dict forwarded as ``params=`` to ``ScrapingBeeClient.get()``.
        headers: dict forwarded as ``headers=`` to ``ScrapingBeeClient.get()``.
            When non-empty, ``params["forward_headers"] = True`` is also set so
            ScrapingBee actually passes them to the target site.
    """
    params: dict = {}
    headers: dict = {}

    # --- JS rendering ---
    params["render_js"] = opts.render_js  # True by default per ScrapeOptions

    # --- Geography ---
    if opts.country:
        params["country_code"] = opts.country

    # --- Wait / timing ---
    if opts.wait_ms != ScrapeOptions().wait_ms or opts.wait_ms:
        # Always forward wait_ms so callers can explicitly set 0 to skip
        params["wait"] = opts.wait_ms

    if opts.wait_for_selector:
        params["wait_for"] = opts.wait_for_selector

    # --- Anti-bot / proxy mode ---
    # stealth_proxy and premium_proxy are mutually exclusive.
    # Stealth wins; emit a warning when both are requested.
    if opts.stealth and opts.premium_proxy:
        logger.warning(
            "scrapingbee: both stealth=True and premium_proxy=True requested; "
            "stealth_proxy takes priority, premium_proxy will be ignored"
        )
        params["stealth_proxy"] = True
    elif opts.stealth:
        params["stealth_proxy"] = True
    elif opts.premium_proxy:
        params["premium_proxy"] = True

    # --- Headers forwarded to target site ---
    if opts.language:
        headers["Accept-Language"] = opts.language

    if opts.user_agent:
        headers["User-Agent"] = opts.user_agent

    if opts.custom_headers:
        headers.update(opts.custom_headers)

    if headers:
        params["forward_headers"] = True

    # --- Cookies ---
    if opts.cookies:
        # ScrapingBee expects a semicolon-delimited "key=value" cookie string
        params["cookies"] = "; ".join(f"{k}={v}" for k, v in opts.cookies.items())

    # --- Screenshot ---
    if opts.take_screenshot:
        full_page = bool(opts.extra.get("full_page")) if opts.extra else False
        if full_page:
            params["screenshot_full_page"] = True
        else:
            params["screenshot"] = True

    # --- Extra ScrapingBee-specific params (scrapingbee_* prefix stripped) ---
    for key, value in (opts.extra or {}).items():
        if key.startswith("scrapingbee_"):
            spb_key = key[len("scrapingbee_") :]
            params[spb_key] = value

    return params, headers


class ScrapingbeeEngine(ScrapeEngine):
    """ScrapingBee-backed scrape engine.

    Requires ``SCRAPINGBEE_API_KEY`` in the environment (or pass ``api_key``
    directly to the constructor). Install the optional extra:
    ``pip install scrapefold[scrapingbee]``.
    """

    NAME = "scrapingbee"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        estimated_cost_usd=0.001,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="residential",
        default_timeout_s=60,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "wait_ms",
            "wait_for_selector",
            "stealth",
            "premium_proxy",
            "user_agent",
            "custom_headers",
            "cookies",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("SCRAPINGBEE_API_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Perform a ScrapingBee API call.

        The SDK's ``ScrapingBeeClient.get()`` is synchronous (uses ``requests``
        internally). We wrap it with ``asyncio.to_thread`` to avoid blocking the
        event loop, keeping ``_fetch`` properly async per the golden rule.
        """
        # Lazy import via helper — only fails if scrapingbee extra is not installed.
        client_cls = _get_client_cls()

        params, headers = _adapt(opts)
        client = client_cls(api_key=self.api_key)

        response = await asyncio.to_thread(
            client.get,
            url,
            params=params,
            headers=headers or None,
            timeout=opts.timeout_s,
        )

        status_code: int = response.status_code
        content_type: str = response.headers.get("content-type", "")

        meta: dict = {
            "status_code": status_code,
            "content_type": content_type,
        }

        spb_resolved = response.headers.get("Spb-Resolved-Url")
        if spb_resolved:
            meta["spb_resolved_url"] = spb_resolved

        # Derive cost from response header when available; fall back to fixed estimate.
        try:
            spb_cost = response.headers.get("Spb-Cost")
            cost_usd = float(spb_cost) * 0.001 if spb_cost else 0.001
        except (TypeError, ValueError):
            cost_usd = 0.001

        # Screenshot mode: the response body IS the PNG bytes.
        if opts.take_screenshot:
            screenshot_b64 = base64.b64encode(response.content).decode()
            return ScrapeResult(
                url=url,
                text="",
                markdown="",
                html=None,
                engine=self.NAME,
                elapsed_ms=0,
                cost_usd=cost_usd,
                screenshot_b64=screenshot_b64,
                meta=meta,
            )

        # HTML / text response path.
        html_body: str | None = None
        text = ""
        markdown = ""

        is_html = "html" in content_type or not content_type
        if is_html and status_code < 400:
            html_body = response.text
            text, markdown = html_to_both(html_body, base_url=url)
        else:
            # Non-HTML or error responses: surface raw text without conversion.
            text = response.text or ""
            markdown = text

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html_body,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=cost_usd,
            meta=meta,
        )


__all__ = ["ScrapingbeeEngine", "_adapt"]
