"""Selenium engine — local headless Chrome browser, no API key required.

Selenium drives a local Chrome browser and returns the fully-rendered page
source after JavaScript execution.  It requires the ``selenium`` extra:

    pip install scrapefold[selenium]

This also installs ``webdriver-manager`` which auto-downloads the matching
``chromedriver`` binary on first use.

Cookie handling (v1):
    Selenium requires a page to be loaded before ``add_cookie()`` can be
    called (domain restriction).  v1 defers cookie support: if ``opts.cookies``
    is supplied the engine logs a DEBUG warning and proceeds without setting
    cookies.  Full cookie support is tracked in the project TODO.

Proxy (v1):
    Selenium proxy configuration requires vendor-specific ``--proxy-server``
    Chrome flags which are complex to generalise.  v1 skips proxy; if a proxy
    is needed use an engine that supports it natively (e.g. ScrapingBee).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy placeholders — populated by ``_load_webdriver()`` on first
# call.  Tests monkeypatch these directly via ``patch.object``.
# ---------------------------------------------------------------------------
webdriver: Any = None
ChromeOptions: Any = None
ChromeService: Any = None
ChromeDriverManager: Any = None

# Separate placeholder for WebDriverWait so tests can patch independently.
WebDriverWait: Any = None
_EC: Any = None  # selenium.webdriver.support.expected_conditions
_By: Any = None  # selenium.webdriver.common.by.By


def _load_webdriver() -> None:
    """Lazily import selenium symbols into module-level globals.

    Called once per process on first ``_fetch`` invocation.  Raises
    ``ImportError`` (propagated as-is) if the ``selenium`` extra is not
    installed — consistent with golden-rule lazy-import behaviour.
    """
    global webdriver, ChromeOptions, ChromeService, ChromeDriverManager
    global WebDriverWait, _EC, _By

    if webdriver is not None:
        return  # already loaded

    from selenium import webdriver as _webdriver  # type: ignore[import-untyped]
    from selenium.webdriver.chrome.options import (  # type: ignore[import-untyped]
        Options as _ChromeOptions,
    )
    from selenium.webdriver.chrome.service import (  # type: ignore[import-untyped]
        Service as _ChromeService,
    )
    from selenium.webdriver.common.by import By as _By_cls  # type: ignore[import-untyped]
    from selenium.webdriver.support import (  # type: ignore[import-untyped]
        expected_conditions as _ec,
    )
    from selenium.webdriver.support.ui import (  # type: ignore[import-untyped]
        WebDriverWait as _WebDriverWait,
    )
    from webdriver_manager.chrome import (  # type: ignore[import-untyped]
        ChromeDriverManager as _ChromeDriverManager,
    )

    webdriver = _webdriver
    ChromeOptions = _ChromeOptions
    ChromeService = _ChromeService
    ChromeDriverManager = _ChromeDriverManager
    WebDriverWait = _WebDriverWait
    _EC = _ec
    _By = _By_cls


class SeleniumEngine(ScrapeEngine):
    """Headless Chrome via Selenium.

    No API key required; no per-call cost.  Best suited for JS-heavy pages
    when no paid SaaS is available or acceptable.

    Limitations (v1):
    - No proxy support (see module docstring).
    - No cookie injection (see module docstring).
    - Single-page only (no native crawl).
    """

    NAME = "selenium"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=True,
        requires_api_key=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        proxy_type="datacenter",
        output_native_markdown=False,
        default_timeout_s=60,
    )
    # custom_headers and cookies are NOT honored by this v1 (selenium can't set
    # arbitrary request headers, and add_cookie requires a prior same-domain
    # navigation). Drop them at the base-class strip stage so callers don't get
    # logged-out content when they thought they were passing a session cookie.
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "render_js",
            "wait_ms",
            "wait_for_selector",
            "user_agent",
            "output_format",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key)
        # Cached after first _fetch — webdriver-manager hits the filesystem
        # (and sometimes the network) every call, so resolve once per engine.
        self._driver_path: str | None = None

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Drive a headless Chrome browser and return the rendered page.

        The synchronous Selenium SDK call is wrapped in ``asyncio.to_thread``
        to avoid blocking the event loop.
        """
        _load_webdriver()
        if self._driver_path is None:
            self._driver_path = ChromeDriverManager().install()
        driver_path = self._driver_path

        def _sync_fetch() -> tuple[str, str | None]:
            """Run the full Selenium session.  Returns (html, screenshot_b64 | None)."""
            chrome_options = ChromeOptions()
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")

            if opts.user_agent:
                chrome_options.add_argument(f"--user-agent={opts.user_agent}")

            if opts.language:
                chrome_options.add_argument(f"--lang={opts.language}")

            service = ChromeService(driver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
            try:
                driver.set_page_load_timeout(opts.timeout_s)

                if opts.cookies:
                    # v1: Cookie injection requires navigating to the domain first
                    # (Selenium enforces same-domain restriction for add_cookie).
                    # Deferred to v2; log so callers are aware.
                    logger.debug(
                        "selenium: cookie injection is deferred (v1 limitation); "
                        "cookies will NOT be set for this request"
                    )

                driver.get(url)

                if opts.wait_ms and opts.wait_ms > 0:
                    time.sleep(opts.wait_ms / 1000)

                if opts.wait_for_selector:
                    WebDriverWait(driver, opts.timeout_s).until(
                        _EC.presence_of_element_located((_By.CSS_SELECTOR, opts.wait_for_selector))
                    )

                html = driver.page_source
                screenshot: str | None = None
                if opts.take_screenshot:
                    screenshot = driver.get_screenshot_as_base64()

                return html, screenshot

            finally:
                driver.quit()

        html, screenshot_b64 = await asyncio.to_thread(_sync_fetch)

        text, markdown = html_to_both(html, base_url=url)

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=0.0,
            screenshot_b64=screenshot_b64,
        )


__all__ = ["SeleniumEngine"]
