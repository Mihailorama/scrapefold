"""JinaEngine — Jina AI Reader-based scrape engine.

Uses the free (or keyed) Jina Reader endpoint at https://r.jina.ai/<url>.
Returns markdown natively, making it ideal for clean content extraction.
Server-side JS rendering is included. No proxy, no stealth.

Free tier is rate-limited; supply JINA_API_KEY for higher rate limits.
"""

from __future__ import annotations

import base64
import logging
import os

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine

# NOTE: We import the private helper _markdown_to_plain_text from html_to_text
# as a one-off to strip markdown markers into plain text. This is the only place
# in the codebase that uses this internal function; if html_to_text is refactored,
# update this import as well.
from scrapefold.html_to_text import (
    _markdown_to_plain_text,
    html_to_both,
)
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_READER_BASE = "https://r.jina.ai/"

# Jina extra keys that map to specific well-known headers (not generic transform)
_KNOWN_EXTRA_KEYS: dict[str, str] = {
    "jina_engine": "X-Engine",
    "jina_timeout": "X-Timeout",
    "jina_no_cache": "X-No-Cache",
    "jina_with_links_summary": "X-With-Links-Summary",
    "jina_with_images_summary": "X-With-Images-Summary",
}


def _adapt_headers(opts: ScrapeOptions, api_key: str | None) -> dict[str, str]:
    """Build the request headers for the Jina Reader call.

    Translates unified ScrapeOptions fields and ``opts.extra["jina_*"]``
    entries into Jina-specific request headers.
    """
    headers: dict[str, str] = {}

    # Authorization — only when a key is available
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Output format / screenshot — take_screenshot wins over output_format
    if opts.take_screenshot:
        headers["X-Return-Format"] = "screenshot"
    elif opts.output_format and opts.output_format != "auto":
        headers["X-Return-Format"] = opts.output_format

    # Localization
    if opts.language:
        headers["Accept-Language"] = opts.language

    # Extra keys: jina_* — known ones map directly, unknown ones use the
    # generic snake-to-kebab transform: jina_foo_bar → X-Foo-Bar
    for key, value in (opts.extra or {}).items():
        if not key.startswith("jina_"):
            continue
        if key in _KNOWN_EXTRA_KEYS:
            headers[_KNOWN_EXTRA_KEYS[key]] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )
        else:
            # Strip "jina_" prefix, split on "_", capitalise each word, join with "-", prefix "X-"
            suffix = key[len("jina_") :]
            header_name = "X-" + "-".join(word.capitalize() for word in suffix.split("_"))
            headers[header_name] = str(value)

    # Caller-provided custom headers override anything we set above
    if opts.custom_headers:
        headers.update(opts.custom_headers)

    return headers


class JinaEngine(ScrapeEngine):
    """Jina AI Reader scrape engine.

    Fetches pages via ``https://r.jina.ai/<url>`` — a free (or keyed) endpoint
    that renders JS server-side and returns clean markdown by default.

    Strong markdown quality; preferred when the target is not anti-bot-protected
    and clean, structured content is needed.
    """

    NAME = "jina"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=True,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="none",
        free_tier=True,
        output_native_markdown=True,
        default_timeout_s=60,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "output_format",
            "take_screenshot",
            "custom_headers",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("JINA_API_KEY"))

    def is_available(self) -> bool:
        # Override: Jina works WITHOUT api_key (free tier)
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* via the Jina Reader and return a ``ScrapeResult``."""
        headers = _adapt_headers(opts, self.api_key)
        return_format = headers.get("X-Return-Format", "markdown")

        async with httpx.AsyncClient(
            timeout=float(opts.timeout_s),
            follow_redirects=True,
        ) as client:
            # Do NOT urlencode the target URL — Jina expects it literal in the path
            response = await client.get(_READER_BASE + url, headers=headers)

        text_out: str
        markdown_out: str
        html_out: str | None = None
        json_out: dict | list | None = None  # type: ignore[type-arg]
        screenshot_b64: str | None = None

        if return_format == "screenshot":
            screenshot_b64 = base64.b64encode(response.content).decode()
            text_out = ""
            markdown_out = ""

        elif return_format == "html":
            html_out = response.text
            text_out, markdown_out = html_to_both(html_out, base_url=url)

        elif return_format == "json":
            try:
                json_out = response.json()
            except Exception:
                logger.debug("engine=jina failed to parse JSON response for %s", url)
                json_out = None
            text_out = str(json_out)
            markdown_out = text_out

        elif return_format == "text":
            text_out = response.text
            markdown_out = response.text
            html_out = None

        else:
            # Default: "markdown" (also covers omitted X-Return-Format, i.e. auto)
            markdown_out = response.text
            html_out = None
            # Derive plain text by stripping markdown markers
            text_out = _markdown_to_plain_text(markdown_out)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=html_out,
            json=json_out,
            screenshot_b64=screenshot_b64,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=0.0,
            meta={"status_code": response.status_code},
        )


__all__ = ["JinaEngine"]
