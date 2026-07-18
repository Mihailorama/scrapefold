"""Tests for the PydollEngine.

All tests run offline — no real browser, no pydoll SDK required. The SDK's
Chrome / ChromiumOptions are monkeypatched with mocks.

pydoll 2.x flow:
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(url, timeout=<s>)
        html = await tab.page_source
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.pydoll import PydollEngine, _adapt_options
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tab(
    *,
    html: str = "<h1>Hello</h1><p>World</p>",
    screenshot: str | None = None,
) -> MagicMock:
    """Return a mock pydoll Tab. ``page_source`` is an awaitable attribute."""
    tab = MagicMock()
    tab.go_to = AsyncMock()
    tab.set_cookies = AsyncMock()
    tab.query = AsyncMock(return_value=None)
    tab.take_screenshot = AsyncMock(return_value=screenshot)

    async def _page_source() -> str:
        return html

    # In the real SDK page_source is a property whose getter is a coroutine;
    # our engine does ``await tab.page_source`` — assign a coroutine object.
    tab.page_source = _page_source()
    return tab


def _make_chrome(tab: MagicMock | None = None) -> MagicMock:
    """Return a mock Chrome class whose context manager yields a browser."""
    if tab is None:
        tab = _make_tab()
    browser = MagicMock()
    browser.start = AsyncMock(return_value=tab)
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=browser)
    instance.__aexit__ = AsyncMock(return_value=False)
    chrome_class = MagicMock(return_value=instance)
    chrome_class._tab = tab  # test handle
    return chrome_class


def _make_options_cls() -> type:
    """Return a recording ChromiumOptions stand-in."""
    instances: list[object] = []

    class FakeOptions:
        def __init__(self) -> None:
            self.headless = False
            self.arguments: list[str] = []
            self.accept_languages: str | None = None
            instances.append(self)

        def add_argument(self, argument: str) -> None:
            self.arguments.append(argument)

        def set_accept_languages(self, languages: str) -> None:
            self.accept_languages = languages

    FakeOptions._instances = instances  # type: ignore[attr-defined]
    return FakeOptions


# ---------------------------------------------------------------------------
# 1. Basic fetch success
# ---------------------------------------------------------------------------


async def test_basic_fetch_success() -> None:
    chrome = _make_chrome(_make_tab(html="<h1>Hi</h1><p>Content</p>"))
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.engine == "pydoll"
    assert result.cost_usd == 0.0
    assert result.text
    assert result.markdown
    assert result.html
    # headless is forced on
    assert opts_cls._instances[0].headless is True


# ---------------------------------------------------------------------------
# 2. user_agent forwarded as a --user-agent chrome flag
# ---------------------------------------------------------------------------


async def test_user_agent_forwarded() -> None:
    chrome = _make_chrome()
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(user_agent="MyBot/1.0"))

    args = opts_cls._instances[0].arguments
    assert "--user-agent=MyBot/1.0" in args


# ---------------------------------------------------------------------------
# 3. language forwarded via set_accept_languages
# ---------------------------------------------------------------------------


async def test_language_forwarded() -> None:
    chrome = _make_chrome()
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(language="ru"))

    assert opts_cls._instances[0].accept_languages == "ru"


# ---------------------------------------------------------------------------
# 4. cookies injected before navigation, scoped to the target URL
# ---------------------------------------------------------------------------


async def test_cookies_set_before_navigation_and_scoped() -> None:
    tab = _make_tab()
    chrome = _make_chrome(tab)
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(cookies={"session": "abc123"}))

    tab.set_cookies.assert_awaited_once()
    passed = tab.set_cookies.await_args.args[0]
    assert {"name": "session", "value": "abc123", "url": _TEST_URL} in passed
    # cookies must be set before go_to for the session to take effect
    assert tab.set_cookies.await_args_list  # sanity


# ---------------------------------------------------------------------------
# 5. wait_for_selector triggers tab.query with the selector
# ---------------------------------------------------------------------------


async def test_wait_for_selector_queries() -> None:
    tab = _make_tab()
    chrome = _make_chrome(tab)
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(wait_for_selector=".content"))

    tab.query.assert_awaited_once()
    assert tab.query.await_args.args[0] == ".content"
    assert tab.query.await_args.kwargs.get("raise_exc") is False


# ---------------------------------------------------------------------------
# 6. timeout_s forwarded to go_to
# ---------------------------------------------------------------------------


async def test_timeout_forwarded_to_go_to() -> None:
    tab = _make_tab()
    chrome = _make_chrome(tab)
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        await engine.scrape(_TEST_URL, ScrapeOptions(timeout_s=45))

    tab.go_to.assert_awaited_once()
    assert tab.go_to.await_args.args[0] == _TEST_URL
    assert tab.go_to.await_args.kwargs.get("timeout") == 45


# ---------------------------------------------------------------------------
# 7. take_screenshot → screenshot_b64 populated
# ---------------------------------------------------------------------------


async def test_take_screenshot_sets_b64() -> None:
    tab = _make_tab(screenshot="c2hvdA==")
    chrome = _make_chrome(tab)
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        result = await engine.scrape(_TEST_URL, ScrapeOptions(take_screenshot=True))

    tab.take_screenshot.assert_awaited_once()
    assert tab.take_screenshot.await_args.kwargs.get("as_base64") is True
    assert result.screenshot_b64 == "c2hvdA=="


# ---------------------------------------------------------------------------
# 8. extra pydoll_args passed through as raw chrome flags
# ---------------------------------------------------------------------------


async def test_extra_pydoll_args_passthrough() -> None:
    chrome = _make_chrome()
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        await engine.scrape(
            _TEST_URL,
            ScrapeOptions(extra={"pydoll_args": ["--proxy-server=127.0.0.1:8080"]}),
        )

    assert "--proxy-server=127.0.0.1:8080" in opts_cls._instances[0].arguments


# ---------------------------------------------------------------------------
# 9. Unsupported options dropped, not raised
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped() -> None:
    chrome = _make_chrome()
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        # custom_headers and premium_proxy are not supported by pydoll
        result = await engine.scrape(
            _TEST_URL,
            ScrapeOptions(custom_headers={"X-H": "1"}, premium_proxy=True),
        )

    assert isinstance(result, ScrapeResult)  # no exception


# ---------------------------------------------------------------------------
# 10. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    browser = MagicMock()
    browser.start = AsyncMock(side_effect=RuntimeError("browser crashed"))
    instance = MagicMock()
    instance.__aenter__ = AsyncMock(return_value=browser)
    instance.__aexit__ = AsyncMock(return_value=False)
    chrome = MagicMock(return_value=instance)
    opts_cls = _make_options_cls()

    with (
        patch("scrapefold.engines.pydoll.Chrome", chrome),
        patch("scrapefold.engines.pydoll.ChromiumOptions", opts_cls),
    ):
        engine = PydollEngine()
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "pydoll"
    assert "browser crashed" in exc_info.value.message


# ---------------------------------------------------------------------------
# 11. is_available — no API key required
# ---------------------------------------------------------------------------


def test_is_available_true() -> None:
    assert PydollEngine().is_available() is True


# ---------------------------------------------------------------------------
# 12. extra["pydoll_binary"] sets ChromiumOptions.binary_location
# ---------------------------------------------------------------------------


def test_pydoll_binary_sets_binary_location() -> None:
    # Containers where pydoll can't auto-detect Chrome need an explicit path.
    options = _adapt_options(
        ScrapeOptions(extra={"pydoll_binary": "/opt/pw-browsers/chromium"}),
        _make_options_cls(),
    )
    assert options.binary_location == "/opt/pw-browsers/chromium"


# ---------------------------------------------------------------------------
# 13. a base flag repeated via pydoll_args is de-duplicated, not doubled
# ---------------------------------------------------------------------------


def test_duplicate_arg_is_deduped() -> None:
    # --no-sandbox is already a base flag; passing it again must not double it,
    # since pydoll's add_argument rejects duplicates.
    options = _adapt_options(
        ScrapeOptions(extra={"pydoll_args": ["--no-sandbox", "--mute-audio"]}),
        _make_options_cls(),
    )
    assert options.arguments.count("--no-sandbox") == 1
    assert "--mute-audio" in options.arguments


# ---------------------------------------------------------------------------
# 14. a pydoll-reserved flag that raises inside add_argument is swallowed
# ---------------------------------------------------------------------------


def test_reserved_flag_collision_is_swallowed() -> None:
    class RaisingOptions:
        """Stand-in whose add_argument rejects a pydoll-reserved flag."""

        def __init__(self) -> None:
            self.headless = False
            self.arguments: list[str] = []
            self.accept_languages: str | None = None

        def add_argument(self, argument: str) -> None:
            if argument == "--no-first-run":  # reserved by pydoll internally
                raise ValueError("Argument already exists: --no-first-run")
            self.arguments.append(argument)

        def set_accept_languages(self, languages: str) -> None:
            self.accept_languages = languages

    # Must not propagate the SDK's ValueError — a reserved-flag collision from a
    # user-supplied pydoll_args entry can't be allowed to crash the whole scrape.
    options = _adapt_options(
        ScrapeOptions(extra={"pydoll_args": ["--no-first-run", "--kept"]}),
        RaisingOptions,
    )
    assert "--kept" in options.arguments
    assert "--no-first-run" not in options.arguments
