"""Firecrawl engine for scrapefold.

Firecrawl SaaS — paid, JS-rendering, returns markdown natively.
Uses the /scrape endpoint for single-URL fetches.

Cost: ~$0.001 per call.
SDK: firecrawl-py (install with ``pip install scrapefold[firecrawl]``).

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require firecrawl-py to be installed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both, html_to_text
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

# Module-level reference to the SDK class, populated lazily on first use.
# Exposed at module scope so tests can monkeypatch it:
#   patch("scrapefold.engines.firecrawl.AsyncFirecrawlApp", ...)
AsyncFirecrawlApp: Any = None


def _load_sdk() -> Any:
    """Return the AsyncFirecrawlApp class, importing it on first call.

    Raises ImportError with an installation hint if firecrawl-py is missing.
    """
    global AsyncFirecrawlApp
    if AsyncFirecrawlApp is not None:
        return AsyncFirecrawlApp
    try:
        import firecrawl as _fc  # lazy
    except ImportError as exc:
        raise ImportError(
            "firecrawl-py is required for FirecrawlEngine. "
            "Install it with: pip install scrapefold[firecrawl]"
        ) from exc
    AsyncFirecrawlApp = _fc.AsyncFirecrawlApp
    return AsyncFirecrawlApp


def _adapt(opts: ScrapeOptions) -> dict[str, Any]:
    """Map unified ScrapeOptions to Firecrawl /scrape params.

    Returns a dict suitable for passing as ``params=`` to
    ``AsyncFirecrawlApp.scrape()``.
    """
    params: dict[str, Any] = {}
    headers = build_target_headers(opts)

    if opts.country:
        params["location"] = {"country": opts.country}

    # Firecrawl always renders JS; render_js=False is a best-effort hint
    # that we pass through by limiting formats to markdown.
    if not opts.render_js:
        params["formats"] = ["markdown"]

    # wait_for_selector (str) takes priority over wait_ms; both map to waitFor.
    if opts.wait_for_selector:
        params["waitFor"] = opts.wait_for_selector
    elif opts.wait_ms != ScrapeOptions().wait_ms:
        params["waitFor"] = opts.wait_ms

    if opts.stealth:
        params["proxy"] = "stealth"
    elif opts.premium_proxy:
        params["proxy"] = "premium"

    if headers:
        params["headers"] = headers

    formats: set[str] = set(params.get("formats", []))
    if not formats:
        if opts.output_format in ("markdown", "auto"):
            formats = {"markdown", "html"}
        elif opts.output_format == "html":
            formats = {"html"}
        elif opts.output_format == "text":
            formats = {"markdown"}
        # "json" is handled separately via /extract; leave formats empty.

    if opts.take_screenshot:
        formats.update({"markdown", "html", "screenshot"} if not formats else {"screenshot"})
    if formats:
        params["formats"] = sorted(formats)

    if opts.timeout_s != ScrapeOptions().timeout_s:
        params["timeout"] = opts.timeout_s * 1000  # Firecrawl uses ms

    params.update(strip_extra_prefix(opts.extra, "firecrawl_"))
    return params


class FirecrawlEngine(ScrapeEngine):
    """Firecrawl SaaS engine.

    Calls Firecrawl's ``/scrape`` endpoint for single-URL fetches and
    ``/extract`` when ``output_format="json"`` + ``extra["schema"]`` is set.

    The SDK (firecrawl-py) is lazy-imported so that importing scrapefold
    doesn't require it to be installed.

    ``screenshot_b64`` is populated with whatever the SDK returns in
    ``Document.screenshot`` — the Firecrawl API returns a base64 data-URI
    (``data:image/png;base64,...``) for the screenshot field.
    """

    NAME = "firecrawl"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        crawl_native=True,
        estimated_cost_usd=0.001,
        billing_unit="call",
        requires_api_key=True,
        proxy_type="datacenter",
        output_native_markdown=True,
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
            "output_format",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("FIRECRAWL_API_KEY"))

    # ------------------------------------------------------------------
    # Engine implementation
    # ------------------------------------------------------------------

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Call Firecrawl and map the response to ScrapeResult.

        Two branches:
        - Default (/scrape): for all output_format values except json-with-schema.
        - Extract (/extract): when output_format="json" AND extra["schema"] is set.
        """
        # Lazy-import the SDK (raises ImportError if not installed)
        app_cls = _load_sdk()
        app = app_cls(api_key=self.api_key)

        schema: dict[str, Any] | None = opts.extra.get("schema") if opts.extra else None
        use_extract = opts.output_format == "json" and schema is not None

        if use_extract and schema is not None:
            return await self._fetch_extract(app, url, opts, schema)
        return await self._fetch_scrape(app, url, opts)

    async def _fetch_scrape(
        self,
        app: Any,
        url: str,
        opts: ScrapeOptions,
    ) -> ScrapeResult:
        """Call /scrape and map Document → ScrapeResult."""
        params = _adapt(opts)
        logger.debug("firecrawl /scrape url=%s params=%s", url, params)

        doc = await app.scrape(url, params=params)

        # --- Text / markdown ---
        markdown = doc.markdown or ""
        html = doc.html or None

        if markdown and html:
            text, _ = html_to_both(html, base_url=url)
        elif html:
            text, markdown = html_to_both(html, base_url=url)
        else:
            text = html_to_text(markdown) if markdown else ""

        # Ensure text is always populated
        if not text and markdown:
            text = markdown

        # --- Screenshot ---
        screenshot_b64: str | None = doc.screenshot or None

        # --- Metadata ---
        meta: dict[str, Any] = {}
        if hasattr(doc, "metadata_dict"):
            meta = doc.metadata_dict or {}
        elif doc.metadata is not None:
            raw = doc.metadata
            if hasattr(raw, "model_dump"):
                meta = raw.model_dump(exclude_none=True)
            elif isinstance(raw, dict):
                meta = raw

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=0.001,
            screenshot_b64=screenshot_b64,
            meta=meta,
        )

    async def _fetch_extract(
        self,
        app: Any,
        url: str,
        opts: ScrapeOptions,
        schema: dict[str, Any],
    ) -> ScrapeResult:
        """Call /extract for structured JSON output and map to ScrapeResult."""
        logger.debug("firecrawl /extract url=%s schema=%s", url, schema)

        extract_result = await app.extract(
            urls=[url],
            schema=schema,
        )

        # extract() returns an object with a .data attribute
        json_data = extract_result.data if hasattr(extract_result, "data") else extract_result

        return ScrapeResult(
            url=url,
            text="",
            markdown="",
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=0.001,
            json=json_data,
        )


__all__ = ["FirecrawlEngine", "_adapt"]
