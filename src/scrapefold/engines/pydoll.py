"""pydoll engine for scrapefold.

pydoll — free, open-source, stealth-first browser automation for Chromium that
drives the browser over the Chrome DevTools Protocol directly, with **no
WebDriver** and no ``navigator.webdriver`` flag. Built to look human, it clears
Cloudflare / Turnstile-style challenges without plugins. No API key required.

SDK: pydoll-python (install with ``pip install scrapefold[pydoll]`` or
     ``pip install 'pydoll-python>=2.0'``).

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require pydoll to be installed.

Introspected surface (pydoll-python 2.x):
  Chrome(options: ChromiumOptions | None = None) — async context manager.
      ``async with Chrome(options=opts) as browser:`` then
      ``tab = await browser.start()`` returns a ``Tab``.
  ChromiumOptions.headless (bool), .add_argument(str), .set_accept_languages(str)
  Tab.go_to(url, timeout=<seconds>)
  Tab.page_source -> awaitable[str]   (property returning a coroutine)
  Tab.set_cookies(list[CookieParam])  (CookieParam accepts name/value/url)
  Tab.query(css, timeout=<seconds>, raise_exc=False)
  Tab.take_screenshot(as_base64=True) -> str

Unsupported options (stripped at the base-class boundary, never reach ``_fetch``):

- ``custom_headers``: setting arbitrary request headers needs CDP
  ``Network.setExtraHTTPHeaders`` wiring that is version-sensitive; left out of
  this v1 so callers are never silently told a header was honored. Use crawl4ai
  or scrapling_stealth when custom request headers are required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import (
    ScrapeOptions,
    cookies_to_playwright_list,
)
from scrapefold.result import ScrapeResult

_DEFAULT_OPTS = ScrapeOptions()

logger = logging.getLogger(__name__)

# Module-level references to SDK classes, populated lazily on first use.
# Exposed at module scope so tests can monkeypatch them:
#   patch("scrapefold.engines.pydoll.Chrome", ...)
#   patch("scrapefold.engines.pydoll.ChromiumOptions", ...)
Chrome: Any = None
ChromiumOptions: Any = None


def _load_sdk() -> tuple[Any, Any]:
    """Return (Chrome, ChromiumOptions), importing pydoll on first call.

    Raises ImportError with an installation hint if pydoll is missing.
    """
    global Chrome, ChromiumOptions
    if Chrome is not None and ChromiumOptions is not None:
        return Chrome, ChromiumOptions
    try:
        from pydoll.browser.chromium import Chrome as _Chrome  # lazy
        from pydoll.browser.options import ChromiumOptions as _ChromiumOptions  # lazy
    except ImportError as exc:
        raise ImportError(
            "pydoll is required for PydollEngine. Install it with: pip install 'pydoll-python>=2.0'"
        ) from exc
    Chrome = _Chrome
    ChromiumOptions = _ChromiumOptions
    return Chrome, ChromiumOptions


def _adapt_options(opts: ScrapeOptions, options_cls: Any) -> Any:
    """Build a ChromiumOptions from browser-level options.

    Always headless with the container-safe sandbox flags. ``user_agent`` and
    ``language`` map to their dedicated ChromiumOptions setters. Extra hooks via
    ``opts.extra``: ``pydoll_binary`` sets a non-default Chrome/Chromium path
    (for containers where auto-detection fails), ``pydoll_args`` appends raw
    Chrome flags (e.g. ``--proxy-server=...``).
    """
    options = options_cls()
    options.headless = True

    def _add(arg: str) -> None:
        # pydoll's add_argument raises on a duplicate flag, and it reserves
        # some flags itself (e.g. --no-first-run). Guard against both: skip a
        # flag already in the list, and swallow the "already exists" raised for
        # pydoll-reserved flags so an overlapping base flag or a user-supplied
        # pydoll_args duplicate can't crash the whole scrape.
        if arg in getattr(options, "arguments", []):
            return
        try:
            options.add_argument(arg)
        except Exception as exc:  # reserved-flag collision inside pydoll
            logger.debug("pydoll skipping duplicate/reserved arg %s: %s", arg, exc)

    _add("--no-sandbox")
    _add("--disable-dev-shm-usage")

    binary = opts.extra.get("pydoll_binary")
    if binary:
        options.binary_location = str(binary)

    # Unified proxy → Chrome's --proxy-server (this is the exit the rotation
    # pool threads in). An explicit --proxy-server via pydoll_args still applies.
    if opts.proxy:
        _add(f"--proxy-server={opts.proxy}")

    if opts.user_agent:
        _add(f"--user-agent={opts.user_agent}")

    if opts.language:
        options.set_accept_languages(opts.language)

    for arg in opts.extra.get("pydoll_args", []):
        _add(str(arg))

    logger.debug(
        "pydoll options headless=True binary=%s args=%s",
        getattr(options, "binary_location", None),
        options.arguments,
    )
    return options


class PydollEngine(ScrapeEngine):
    """pydoll stealth Chromium engine (CDP-driven, no WebDriver).

    Runs a local Chromium over the Chrome DevTools Protocol with a human-like
    fingerprint that clears Cloudflare / Turnstile challenges without plugins.
    Free to use — no API key required.

    The SDK (pydoll-python) is lazy-imported so that importing scrapefold
    doesn't require it to be installed.
    """

    NAME = "pydoll"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        crawl_native=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="datacenter",
        avg_response_mb_estimate=15.0,  # full browser session
        output_native_markdown=False,
        default_timeout_s=60,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "render_js",
            "wait_ms",
            "wait_for_selector",
            "user_agent",
            "cookies",
            "output_format",
            "take_screenshot",
            "timeout_s",
            "proxy",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        # No API key needed; accept it for interface consistency only.
        super().__init__(api_key)

    def is_available(self) -> bool:
        """pydoll requires no API key — always available if SDK is installed."""
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Drive a headless stealth Chromium and return the rendered page."""
        chrome_cls, options_cls = _load_sdk()
        options = _adapt_options(opts, options_cls)

        logger.debug("pydoll fetch url=%s timeout_s=%s", url, opts.timeout_s)

        async with chrome_cls(options=options) as browser:
            tab = await browser.start()

            # Cookies must be injected before navigation so an authenticated
            # session is in effect when the target page loads.
            if opts.cookies:
                await tab.set_cookies(cookies_to_playwright_list(opts.cookies, url))

            await tab.go_to(url, timeout=opts.timeout_s)

            if opts.wait_ms and opts.wait_ms > 0:
                await asyncio.sleep(opts.wait_ms / 1000)

            if opts.wait_for_selector:
                # raise_exc=False → a missing selector is not fatal; we still
                # return whatever rendered rather than failing the whole walk.
                await tab.query(opts.wait_for_selector, timeout=opts.timeout_s, raise_exc=False)

            html = str(await tab.page_source)

            screenshot_b64: str | None = None
            if opts.take_screenshot:
                screenshot_b64 = await tab.take_screenshot(as_base64=True) or None

        text, markdown = html_to_both(html, base_url=url)

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=0.0,
            screenshot_b64=screenshot_b64,
        )


__all__ = ["PydollEngine", "_adapt_options"]
