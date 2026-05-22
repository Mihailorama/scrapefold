"""Tests for the Crawl4AIEngine.

All tests run offline — no real network calls, no crawl4ai SDK required for
most tests. The SDK's AsyncWebCrawler is monkeypatched with an AsyncMock.

crawl4ai 0.8.x config split:
- BrowserConfig: user_agent, headers, cookies → AsyncWebCrawler(config=browser_config)
- CrawlerRunConfig: wait, screenshot, timeout, extra → arun(url, config=run_config)

Test 11 is the regression-guard: it uses ``pytest.importorskip("crawl4ai")``
and introspects the *real* CrawlerRunConfig.__init__ + BrowserConfig.__init__ to
verify that every kwarg the engine passes is a valid parameter name.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.crawl4ai import Crawl4AIEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://example.com"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crawl_result(
    *,
    html: str = "<h1>Hello</h1><p>World</p>",
    markdown_raw: str = "# Hello\n\nWorld",
    screenshot: str | None = None,
    success: bool = True,
) -> MagicMock:
    """Return a minimal mock of crawl4ai's CrawlResult."""
    result = MagicMock()
    result.html = html
    result.success = success
    result.markdown = markdown_raw
    result.screenshot = screenshot
    result.error_message = None
    return result


def _make_container_mock(crawl_result: MagicMock) -> MagicMock:
    """Return a mock of the CrawlResultContainer returned by arun()."""
    container = MagicMock()
    container.__getitem__ = MagicMock(return_value=crawl_result)
    container.html = crawl_result.html
    container.markdown = crawl_result.markdown
    container.screenshot = crawl_result.screenshot
    container.success = crawl_result.success
    container.error_message = crawl_result.error_message
    return container


def _make_crawler_mock(crawl_result: MagicMock | None = None) -> MagicMock:
    """Return a mock for AsyncWebCrawler class (not instance)."""
    if crawl_result is None:
        crawl_result = _make_crawl_result()
    container = _make_container_mock(crawl_result)
    instance = MagicMock()
    instance.arun = AsyncMock(return_value=container)
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    crawler_class = MagicMock(return_value=instance)
    return crawler_class


def _make_run_config_mock() -> type:
    """Return a mock CrawlerRunConfig class that records constructor kwargs."""
    instances: list[MagicMock] = []

    class MockRunConfig(MagicMock):
        def __init__(self, **kwargs: object) -> None:  # type: ignore[override]
            super().__init__()
            self._init_kwargs = kwargs
            for k, v in kwargs.items():
                object.__setattr__(self, k, v)
            instances.append(self)

    MockRunConfig._instances = instances  # type: ignore[attr-defined]
    return MockRunConfig


def _make_browser_config_mock() -> type:
    """Return a mock BrowserConfig class that records constructor kwargs."""
    instances: list[MagicMock] = []

    class MockBrowserConfig(MagicMock):
        def __init__(self, **kwargs: object) -> None:  # type: ignore[override]
            super().__init__()
            self._init_kwargs = kwargs
            for k, v in kwargs.items():
                object.__setattr__(self, k, v)
            instances.append(self)

    MockBrowserConfig._instances = instances  # type: ignore[attr-defined]
    return MockBrowserConfig


# ---------------------------------------------------------------------------
# 1. Basic fetch success
# ---------------------------------------------------------------------------


async def test_basic_fetch_success() -> None:
    crawl_result = _make_crawl_result(
        html="<h1>Hi</h1><p>Content</p>",
        markdown_raw="# Hi\n\nContent",
    )
    crawler_class = _make_crawler_mock(crawl_result)
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.engine == "crawl4ai"
    assert result.cost_usd == 0.0
    assert result.text
    assert result.markdown


# ---------------------------------------------------------------------------
# 2. user_agent forwarded into BrowserConfig
# ---------------------------------------------------------------------------


async def test_user_agent_forwarded() -> None:
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(user_agent="MyBot/1.0"))

    assert len(browse_cfg_cls._instances) == 1
    assert browse_cfg_cls._instances[0].user_agent == "MyBot/1.0"


# ---------------------------------------------------------------------------
# 3. cookies forwarded into BrowserConfig as Playwright list format
# ---------------------------------------------------------------------------


async def test_cookies_scoped_to_target_url() -> None:
    """Playwright requires cookie ``url`` to match the navigation target; using
    a placeholder like ``about:blank`` makes Playwright drop the cookie before
    arun() loads the real page."""
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(cookies={"session": "abc123"}))

    assert len(browse_cfg_cls._instances) == 1
    cookies = browse_cfg_cls._instances[0].cookies
    assert isinstance(cookies, list)
    assert {"name": "session", "value": "abc123", "url": _TEST_URL} in cookies


# ---------------------------------------------------------------------------
# 4. custom_headers forwarded into BrowserConfig.headers
# ---------------------------------------------------------------------------


async def test_custom_headers_forwarded() -> None:
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(custom_headers={"X-My-Header": "val"}))

    assert len(browse_cfg_cls._instances) == 1
    headers = browse_cfg_cls._instances[0].headers
    assert isinstance(headers, dict)
    assert headers.get("X-My-Header") == "val"


# ---------------------------------------------------------------------------
# 5. wait_ms forwarded as delay_before_return_html (seconds) in CrawlerRunConfig
# ---------------------------------------------------------------------------


async def test_wait_ms_forwarded() -> None:
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(wait_ms=3000))

    assert len(run_cfg_cls._instances) == 1
    assert run_cfg_cls._instances[0].delay_before_return_html == pytest.approx(3.0)


async def test_timeout_forwarded_even_at_default() -> None:
    """Regression: page_timeout is always set so crawl4ai's internal default
    cannot win silently when ``timeout_s`` matches scrapefold's default.
    """
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions())

    assert run_cfg_cls._instances[0].page_timeout == ScrapeOptions().timeout_s * 1000


# ---------------------------------------------------------------------------
# 6. wait_for_selector forwarded as wait_for in CrawlerRunConfig
# ---------------------------------------------------------------------------


async def test_wait_for_selector_forwarded() -> None:
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(wait_for_selector=".content"))

    assert len(run_cfg_cls._instances) == 1
    assert run_cfg_cls._instances[0].wait_for == ".content"


# ---------------------------------------------------------------------------
# 7. take_screenshot=True → screenshot=True in CrawlerRunConfig + screenshot_b64
# ---------------------------------------------------------------------------


async def test_take_screenshot_sets_screenshot_b64() -> None:
    crawl_result = _make_crawl_result(screenshot="data:image/png;base64,iVBORw==")
    crawler_class = _make_crawler_mock(crawl_result)
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        result = await engine.scrape(_TEST_URL, ScrapeOptions(take_screenshot=True))

    assert len(run_cfg_cls._instances) == 1
    assert run_cfg_cls._instances[0].screenshot is True
    assert result.screenshot_b64 == "data:image/png;base64,iVBORw=="


# ---------------------------------------------------------------------------
# 8. Unsupported options are dropped (not raised)
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped() -> None:
    """Options outside SUPPORTED_OPTIONS must be silently dropped."""
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        # stealth and premium_proxy are not supported by crawl4ai
        result = await engine.scrape(
            _TEST_URL,
            ScrapeOptions(stealth=True, premium_proxy=True),
        )

    assert isinstance(result, ScrapeResult)
    # No exception raised — engine drops unsupported opts silently


# ---------------------------------------------------------------------------
# 9. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    instance = MagicMock()
    instance.arun = AsyncMock(side_effect=RuntimeError("Playwright crashed"))
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    crawler_class = MagicMock(return_value=instance)
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "crawl4ai"
    assert "Playwright crashed" in exc_info.value.message


# ---------------------------------------------------------------------------
# 10. is_available returns True — no API key required
# ---------------------------------------------------------------------------


def test_is_available_true() -> None:
    engine = Crawl4AIEngine()
    assert engine.is_available() is True


# ---------------------------------------------------------------------------
# 11. Regression guard: kwargs passed to CrawlerRunConfig and BrowserConfig
#     match the real SDK signatures.
# ---------------------------------------------------------------------------


def test_regression_kwargs_match_sdk_signature() -> None:
    """Guard against silently dropping options due to wrong param names.

    Requires crawl4ai to be installed (skipped otherwise).

    Strategy: call the engine's private adapter functions directly with
    mock config classes that record constructor kwargs, then compare those
    kwargs against the *real* CrawlerRunConfig/BrowserConfig signatures.

    We do NOT instantiate the real config classes because CrawlerRunConfig
    0.8.x has a buggy __setattr__ that calls inspect.signature(self.__init__)
    — overriding __init__ in a subclass triggers a KeyError inside that hook.
    Calling the adapters with mock classes avoids the issue while still
    validating every kwarg name against the real signature.
    """
    pytest.importorskip("crawl4ai")
    from crawl4ai.async_configs import BrowserConfig as RealBC
    from crawl4ai.async_configs import CrawlerRunConfig as RealCRC

    import scrapefold.engines.crawl4ai as engine_mod

    crc_params = set(inspect.signature(RealCRC.__init__).parameters.keys()) - {"self"}
    bc_params = set(inspect.signature(RealBC.__init__).parameters.keys()) - {"self"}

    captured_run: dict = {}
    captured_browser: dict = {}

    # Lightweight recording stubs — no inheritance from the real classes,
    # so crawl4ai's broken __setattr__ hook is never triggered.
    class RecordingCRC:
        def __init__(self, **kwargs):  # type: ignore[override]
            captured_run.update(kwargs)

    class RecordingBC:
        def __init__(self, **kwargs):  # type: ignore[override]
            captured_browser.update(kwargs)

    opts = ScrapeOptions(
        user_agent="TestAgent",
        wait_ms=2000,
        wait_for_selector=".foo",
        take_screenshot=True,
        timeout_s=45,
        cookies={"k": "v"},
        custom_headers={"X-H": "1"},
        language="en",
        extra={"crawl4ai_word_count_threshold": 10},
    )

    # Drive both adapters with recording stubs.
    engine_mod._adapt_run_config(opts, RecordingCRC)
    engine_mod._adapt_browser_config(opts, RecordingBC, _TEST_URL)

    invalid_run = set(captured_run.keys()) - crc_params
    assert not invalid_run, (
        f"Engine passed kwargs to CrawlerRunConfig not in its signature: {invalid_run!r}. "
        "Check _adapt_run_config() — these will error or be silently ignored by crawl4ai."
    )

    invalid_browser = set(captured_browser.keys()) - bc_params
    assert not invalid_browser, (
        f"Engine passed kwargs to BrowserConfig not in its signature: {invalid_browser!r}. "
        "Check _adapt_browser_config() — these will error or be silently ignored by crawl4ai."
    )


# ---------------------------------------------------------------------------
# 12. extra crawl4ai_* passthrough into CrawlerRunConfig
# ---------------------------------------------------------------------------


async def test_extra_crawl4ai_passthrough() -> None:
    """extra={"crawl4ai_word_count_threshold": 50} → CrawlerRunConfig(word_count_threshold=50)."""
    crawler_class = _make_crawler_mock()
    run_cfg_cls = _make_run_config_mock()
    browse_cfg_cls = _make_browser_config_mock()

    with (
        patch("scrapefold.engines.crawl4ai.AsyncWebCrawler", crawler_class),
        patch("scrapefold.engines.crawl4ai.CrawlerRunConfig", run_cfg_cls),
        patch("scrapefold.engines.crawl4ai.BrowserConfig", browse_cfg_cls),
    ):
        engine = Crawl4AIEngine()
        await engine.scrape(
            _TEST_URL,
            ScrapeOptions(extra={"crawl4ai_word_count_threshold": 50}),
        )

    assert len(run_cfg_cls._instances) == 1
    assert run_cfg_cls._instances[0].word_count_threshold == 50
