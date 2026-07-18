"""Camoufox engine for scrapefold.

Camoufox — free, open-source anti-detect browser built on Firefox for web
scraping and AI agents. Its stealth patches live at the browser source level
(not injected JS), so it passes Cloudflare / reCAPTCHA / fingerprinting tests
that a patched Chromium leaks on. A drop-in Playwright browser. No API key.

Camoufox is scrapefold's only Firefox-based stealth path — every other stealth
engine (scrapling_stealth, pydoll, cloakbrowser) is Chromium-based, so Camoufox
gives the ladder fingerprint diversity a second Chromium never would.

SDK: camoufox (install with ``pip install scrapefold[camoufox]`` or
     ``pip install 'camoufox[geoip]>=0.4'``). The Firefox build is downloaded
     once via ``python -m camoufox fetch``.

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require camoufox to be installed.

Introspected surface (camoufox 0.5.x):
  AsyncCamoufox(**launch_options) — async context manager yielding a Playwright
      ``Browser``. Key launch options: headless, humanize, locale, proxy, os,
      geoip, block_images, fingerprint. Extra options pass through via the
      ``camoufox_*`` prefix on ``opts.extra``.
  The returned Browser is a standard Playwright object: new_context / new_page /
      goto / content / screenshot / wait_for_selector.

Unsupported options (stripped at the base-class boundary, never reach ``_fetch``):

- ``user_agent``: Camoufox derives the User-Agent from a coherent generated
  fingerprint. Overriding it desyncs the fingerprint and defeats the stealth
  that is the whole reason to use Camoufox, so it is intentionally dropped.
  Pass ``extra={"camoufox_os": "windows"}`` / a ``camoufox_fingerprint`` to
  steer the identity instead.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import (
    ScrapeOptions,
    build_target_headers,
    cookies_to_playwright_list,
    strip_extra_prefix,
)
from scrapefold.result import ScrapeResult

_DEFAULT_OPTS = ScrapeOptions()

logger = logging.getLogger(__name__)

# Module-level reference populated lazily on first use.
# Tests monkeypatch: patch("scrapefold.engines.camoufox.AsyncCamoufox", mock_cls)
AsyncCamoufox: Any = None


def _load_sdk() -> Any:
    """Return the AsyncCamoufox class, importing camoufox on first call.

    Raises ImportError with an installation hint if camoufox is missing.
    """
    global AsyncCamoufox
    if AsyncCamoufox is not None:
        return AsyncCamoufox
    try:
        from camoufox.async_api import AsyncCamoufox as _AsyncCamoufox  # lazy
    except ImportError as exc:
        raise ImportError(
            "camoufox is required for CamoufoxEngine. "
            "Install it with: pip install 'camoufox[geoip]>=0.4' "
            "then: python -m camoufox fetch"
        ) from exc
    AsyncCamoufox = _AsyncCamoufox
    return AsyncCamoufox


def _adapt_launch(opts: ScrapeOptions) -> dict[str, Any]:
    """Map unified ScrapeOptions to AsyncCamoufox launch kwargs.

    Base: headless + humanize (human-like cursor movement). ``language`` maps
    to Firefox ``locale``. Any ``camoufox_*`` key in ``opts.extra`` (e.g.
    ``camoufox_proxy``, ``camoufox_os``, ``camoufox_geoip``) passes through.
    """
    kwargs: dict[str, Any] = {"headless": True, "humanize": True}

    if opts.language:
        kwargs["locale"] = opts.language

    kwargs.update(strip_extra_prefix(opts.extra, "camoufox_"))
    return kwargs


class CamoufoxEngine(ScrapeEngine):
    """Camoufox anti-detect Firefox engine (Playwright-driven).

    Runs a stealth-patched Firefox whose fingerprint is coherent at the source
    level, clearing anti-bot walls that leak on patched-Chromium engines. Free
    to use — no API key required.

    The SDK (camoufox) is lazy-imported so that importing scrapefold doesn't
    require it to be installed.
    """

    NAME = "camoufox"
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
            "wait_until",
            "custom_headers",
            "cookies",
            "output_format",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        # No API key needed; accept it for interface consistency only.
        super().__init__(api_key)

    def is_available(self) -> bool:
        """Camoufox requires no API key — always available if SDK is installed."""
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Drive a stealth Firefox via Camoufox and return the rendered page."""
        camoufox_cls = _load_sdk()
        launch_kwargs = _adapt_launch(opts)

        logger.debug("camoufox fetch url=%s launch=%s", url, list(launch_kwargs))

        # user_agent / cookies are handled by the browser context, not headers.
        headers = build_target_headers(opts, include_cookies=False, include_user_agent=False)

        async with camoufox_cls(**launch_kwargs) as browser:
            context = await browser.new_context()

            if opts.cookies:
                await context.add_cookies(cookies_to_playwright_list(opts.cookies, url))

            page = await context.new_page()

            if headers:
                await page.set_extra_http_headers(headers)

            await page.goto(
                url,
                timeout=opts.timeout_s * 1000,  # Playwright expects ms
                wait_until=opts.wait_until,
            )

            if opts.wait_ms and opts.wait_ms > 0:
                await page.wait_for_timeout(opts.wait_ms)

            if opts.wait_for_selector:
                try:
                    await page.wait_for_selector(
                        opts.wait_for_selector, timeout=opts.timeout_s * 1000
                    )
                except Exception:  # missing selector isn't fatal — return what rendered
                    logger.debug("camoufox wait_for_selector timed out: %s", opts.wait_for_selector)

            html = await page.content()

            screenshot_b64: str | None = None
            if opts.take_screenshot:
                png = await page.screenshot()
                screenshot_b64 = base64.b64encode(png).decode("ascii")

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


__all__ = ["CamoufoxEngine", "_adapt_launch"]
