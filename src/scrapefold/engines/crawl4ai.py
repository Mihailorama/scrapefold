"""crawl4ai engine for scrapefold.

crawl4ai — free, open-source, Playwright-based crawler with native markdown output.
No API key required. Produces markdown natively via its own markdown generation pipeline.

SDK: crawl4ai (install with ``pip install scrapefold[crawl4ai]`` or ``pip install crawl4ai>=0.8``).

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require crawl4ai to be installed.

Architecture note (crawl4ai 0.8.x):
- Browser-level config (headers, cookies, user_agent) goes into BrowserConfig,
  which is passed to AsyncWebCrawler(config=browser_config).
- Run-level config (wait, screenshot, timeout) goes into CrawlerRunConfig,
  which is passed to crawler.arun(url, config=run_config).
"""

from __future__ import annotations

import logging
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both, markdown_to_text
from scrapefold.options import (
    ScrapeOptions,
    build_target_headers,
    cookies_to_playwright_list,
    strip_extra_prefix,
)
from scrapefold.result import ScrapeResult

_DEFAULT_OPTS = ScrapeOptions()

logger = logging.getLogger(__name__)

# Module-level references to SDK classes, populated lazily on first use.
# Exposed at module scope so tests can monkeypatch them:
#   patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", ...)
#   patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", ...)
AsyncWebCrawler: Any = None
CrawlerRunConfig: Any = None
BrowserConfig: Any = None


def _load_sdk() -> tuple[Any, Any, Any]:
    """Return (AsyncWebCrawler, CrawlerRunConfig, BrowserConfig), importing on first call.

    Raises ImportError with an installation hint if crawl4ai is missing.
    """
    global AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
    if AsyncWebCrawler is not None and CrawlerRunConfig is not None and BrowserConfig is not None:
        return AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
    try:
        import crawl4ai as _c4a  # lazy
        from crawl4ai.async_configs import BrowserConfig as _BrowserConfig  # lazy
        from crawl4ai.async_configs import CrawlerRunConfig as _CrawlerRunConfig  # lazy
    except ImportError as exc:
        raise ImportError(
            "crawl4ai is required for Crawl4AIEngine. Install it with: pip install 'crawl4ai>=0.8'"
        ) from exc
    AsyncWebCrawler = _c4a.AsyncWebCrawler
    CrawlerRunConfig = _CrawlerRunConfig
    BrowserConfig = _BrowserConfig
    return AsyncWebCrawler, CrawlerRunConfig, BrowserConfig


def _adapt_browser_config(opts: ScrapeOptions, browser_config_cls: Any, url: str) -> Any | None:
    """Build a BrowserConfig from browser-level options.

    BrowserConfig handles: user_agent, headers (including Accept-Language,
    custom_headers), and cookies (Playwright list format).

    Returns None when no browser-level overrides are needed so the caller
    can use the default AsyncWebCrawler() constructor.
    """
    kwargs: dict[str, Any] = {}

    if opts.user_agent:
        kwargs["user_agent"] = opts.user_agent

    # user_agent and cookies are dedicated BrowserConfig kwargs — keep them
    # out of the headers dict so the SDK manages them natively.
    headers = build_target_headers(opts, include_cookies=False, include_user_agent=False)
    if headers:
        kwargs["headers"] = headers

    if opts.cookies:
        kwargs["cookies"] = cookies_to_playwright_list(opts.cookies, url)

    if not kwargs:
        return None

    logger.debug("crawl4ai BrowserConfig kwargs=%s", kwargs)
    return browser_config_cls(**kwargs)


def _adapt_run_config(opts: ScrapeOptions, run_config_cls: Any) -> Any:
    """Build a CrawlerRunConfig from run-level options.

    CrawlerRunConfig handles: wait, wait_for_selector, screenshot, timeout,
    and extra crawl4ai_* passthrough keys.

    All kwarg names are verified against CrawlerRunConfig.__init__ signature
    (crawl4ai 0.8.x).
    """
    kwargs: dict[str, Any] = {}

    # wait_ms → delay_before_return_html (seconds, float)
    if opts.wait_ms != _DEFAULT_OPTS.wait_ms:
        kwargs["delay_before_return_html"] = opts.wait_ms / 1000.0

    # wait_for_selector → wait_for (CrawlerRunConfig accepts a CSS selector string)
    if opts.wait_for_selector:
        kwargs["wait_for"] = opts.wait_for_selector

    # --- Output ---
    if opts.take_screenshot:
        kwargs["screenshot"] = True

    # --- Timeout ---
    # Always forward so crawl4ai's internal default cannot win silently when
    # timeout_s matches scrapefold's default. CrawlerRunConfig.page_timeout
    # is in milliseconds.
    kwargs["page_timeout"] = opts.timeout_s * 1000

    # --- Extra: crawl4ai_* prefix passthrough ---
    extra_kwargs = strip_extra_prefix(opts.extra, "crawl4ai_")
    kwargs.update(extra_kwargs)

    logger.debug("crawl4ai CrawlerRunConfig kwargs=%s", kwargs)
    return run_config_cls(**kwargs)


class Crawl4AIEngine(ScrapeEngine):
    """crawl4ai Playwright-based crawler engine.

    Uses AsyncWebCrawler.arun() for single-URL fetches. crawl4ai produces
    markdown natively; when HTML is also available, both text and markdown
    are derived from the HTML via html_to_both() for consistency.

    No API key is required — crawl4ai runs locally via Playwright.

    Config split (crawl4ai 0.8.x):
    - BrowserConfig: user_agent, headers, cookies → AsyncWebCrawler(config=...)
    - CrawlerRunConfig: wait, screenshot, timeout, extra → arun(url, config=...)
    """

    NAME = "crawl4ai"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=True,
        crawl_native=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="datacenter",
        output_native_markdown=True,
        default_timeout_s=60,
        avg_response_mb_estimate=15.0,  # full browser session
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "render_js",
            "wait_ms",
            "wait_for_selector",
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
        # No API key needed; accept it for interface consistency only.
        super().__init__(api_key)

    def is_available(self) -> bool:
        """crawl4ai requires no API key — always available if SDK is installed."""
        return True

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Call crawl4ai and map the response to ScrapeResult."""
        awc_cls, run_config_cls, browser_config_cls = _load_sdk()

        browser_cfg = _adapt_browser_config(opts, browser_config_cls, url)
        run_cfg = _adapt_run_config(opts, run_config_cls)
        logger.debug("crawl4ai arun url=%s run_config=%s", url, run_cfg)

        crawler_kwargs = {"config": browser_cfg} if browser_cfg is not None else {}
        async with awc_cls(**crawler_kwargs) as crawler:
            container = await crawler.arun(url, config=run_cfg)

        # crawl4ai returns a CrawlResultContainer; unwrap to the first result.
        # For a single URL, result is accessible via container[0] in 0.8.x.
        try:
            result = container[0]
        except (TypeError, IndexError):
            # Fallback: the container itself may be the result (mock or older API).
            result = container

        # --- Text + Markdown ---
        html: str | None = getattr(result, "html", None) or None
        # result.markdown is a StringCompatibleMarkdown (str subclass) or a plain str.
        native_markdown: str = str(getattr(result, "markdown", "") or "")

        if html:
            text, markdown = html_to_both(html, base_url=url)
            # Prefer native markdown from crawl4ai if it's non-empty and richer.
            if native_markdown and len(native_markdown) > len(markdown):
                markdown = native_markdown
                text = markdown_to_text(markdown)
        elif native_markdown:
            markdown = native_markdown
            text = markdown_to_text(markdown)
        else:
            text = ""
            markdown = ""

        # Last-ditch fallback so both slots are non-empty when achievable.
        if not markdown and text:
            markdown = text

        # --- Screenshot ---
        screenshot_b64: str | None = None
        if opts.take_screenshot:
            screenshot_b64 = getattr(result, "screenshot", None) or None

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


__all__ = ["Crawl4AIEngine"]
