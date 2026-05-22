"""CloakBrowser engine for scrapefold.

Local stealth browser via the cloakbrowser SDK (Playwright + fingerprint
hardening). Runs entirely locally — no API key required, no per-call cost.

SDK: cloakbrowser (``pip install 'cloakbrowser>=0.3'``).

Usage pattern:
    ctx = await cloakbrowser.launch_context_async(
        headless=True,
        stealth_args=True,    # fingerprint hardening, always on
        user_agent=...,       # optional UA override
        locale=...,           # from opts.language
        proxy=...,            # ProxySettings or URL string
        extra_http_headers={...},  # forwarded to browser.new_context()
    )
    page = await ctx.new_page()
    await page.goto(url, wait_until="load", timeout=<ms>)
    html = await page.content()
    screenshot_bytes = await page.screenshot(type="png")   # optional
    await ctx.close()

Contract pinned against cloakbrowser 0.3.28.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

# Module-level reference populated lazily on first use.
# Exposed at module scope so tests can monkeypatch it:
#   patch("scrapefold.engines.cloakbrowser.launch_context_async", ...)
launch_context_async: Any = None


def _load_sdk() -> Any:
    """Return cloakbrowser.launch_context_async, importing on first call.

    Raises ImportError with an installation hint if cloakbrowser is missing.
    """
    global launch_context_async
    if launch_context_async is not None:
        return launch_context_async
    try:
        import cloakbrowser as _cb  # lazy
    except ImportError as exc:
        raise ImportError(
            "cloakbrowser is required for CloakBrowserEngine. "
            "Install it with: pip install 'cloakbrowser>=0.3'"
        ) from exc
    launch_context_async = _cb.launch_context_async
    return launch_context_async


def _build_proxy(opts: ScrapeOptions) -> Any | None:
    """Build a cloakbrowser ProxySettings or proxy URL, or None.

    When ``premium_proxy=True`` is set alongside a ``country`` hint, we
    construct a ProxySettings object that targets a residential proxy matching
    that country. Without a country hint, a generic residential proxy flag is
    set by returning a sentinel string that operators can intercept via
    ``extra["cloakbrowser_proxy"]``.

    In practice, callers that run their own proxy service should pass the
    proxy URL via ``extra["cloakbrowser_proxy"]`` and this function is only
    the fallback.
    """
    # Explicit proxy URL wins over everything
    if opts.extra:
        explicit = opts.extra.get("cloakbrowser_proxy")
        if explicit:
            return explicit

    if not opts.premium_proxy:
        return None

    # premium_proxy=True but no concrete server — return a sentinel.
    # This tells the engine there is *some* proxy intent; real deployments
    # override via extra["cloakbrowser_proxy"].
    server = f"socks5://residential-proxy/{opts.country or 'any'}"
    try:
        import cloakbrowser as _cb

        return _cb.ProxySettings(server=server)
    except ImportError:
        return server


def _adapt(opts: ScrapeOptions) -> dict[str, Any]:
    """Map ScrapeOptions to cloakbrowser.launch_context_async kwargs."""
    kwargs: dict[str, Any] = {
        "headless": True,
        "stealth_args": True,  # always on — this is a stealth browser
        "humanize": False,  # deterministic default; callers can override via extra
    }

    if opts.user_agent:
        kwargs["user_agent"] = opts.user_agent

    if opts.language:
        kwargs["locale"] = opts.language

    proxy = _build_proxy(opts)
    if proxy is not None:
        kwargs["proxy"] = proxy

    # Merge headers: Accept-Language, Cookie, custom_headers
    headers = build_target_headers(opts)
    if headers:
        kwargs["extra_http_headers"] = headers

    # timeout_s drives page.goto timeout (SDK uses ms)
    # We keep it here so _fetch can read it separately from kwargs
    # (launch_context_async does not have a timeout param)

    # cloakbrowser_* extra keys forwarded as top-level kwargs
    extra_overrides = strip_extra_prefix(opts.extra, "cloakbrowser_")
    # Don't double-set proxy via the prefix strip — we handled it above
    extra_overrides.pop("proxy", None)
    kwargs.update(extra_overrides)

    return kwargs


class CloakBrowserEngine(ScrapeEngine):
    """Stealth browser engine powered by the cloakbrowser SDK.

    Runs a local Chromium instance with fingerprint-hardening args. No API
    key is required; cost per call is 0 (beyond local compute).

    Ideal for Cloudflare-protected pages and sites that detect headless
    browsers via standard CDP/navigator fingerprints.

    The SDK (cloakbrowser) is lazy-imported so that importing scrapefold
    doesn't require it to be installed.
    """

    NAME = "cloakbrowser"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="residential",
        output_native_markdown=False,
        default_timeout_s=90,
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
        # api_key accepted for API compatibility but not used
        super().__init__(api_key)

    # ------------------------------------------------------------------
    # Engine implementation
    # ------------------------------------------------------------------

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Launch a stealth browser context, navigate to URL, return ScrapeResult."""
        _load_sdk()  # raises ImportError with hint if missing

        launch_fn = launch_context_async
        sdk_kwargs = _adapt(opts)

        timeout_ms = int(opts.timeout_s * 1000)
        wait_until = "load"

        logger.debug("cloakbrowser launch url=%s kwargs=%s", url, sdk_kwargs)

        ctx = await launch_fn(**sdk_kwargs)
        try:
            page = await ctx.new_page()

            # wait_for_selector handled via Playwright's wait_for_selector after goto
            goto_kwargs: dict[str, Any] = {
                "wait_until": wait_until,
                "timeout": timeout_ms,
            }
            await page.goto(url, **goto_kwargs)

            if opts.wait_for_selector:
                await page.wait_for_selector(
                    opts.wait_for_selector,
                    timeout=timeout_ms,
                )
            elif opts.wait_ms != ScrapeOptions().wait_ms:
                await asyncio.sleep(opts.wait_ms / 1000)

            html: str = await page.content()
            text, markdown = html_to_both(html, base_url=url)

            screenshot_b64: str | None = None
            if opts.take_screenshot:
                png_bytes: bytes = await page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")

        finally:
            await ctx.close()

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


__all__ = ["CloakBrowserEngine"]
