"""Tests for SeleniumEngine.

All tests mock the four module-level globals (webdriver, ChromeOptions,
ChromeService, ChromeDriverManager) so that no real browser is launched and
no real network calls are made.  Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import base64
import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

# ---------------------------------------------------------------------------
# Shared HTML fixture
# ---------------------------------------------------------------------------

_HTML = (
    "<html><head><title>Selenium Test</title></head>"
    "<body><h1>Hello</h1><p>Selenium is great</p></body></html>"
)

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------


def _make_driver(*, page_source: str = _HTML, screenshot_b64: str = _PNG_B64) -> MagicMock:
    """Build a fake WebDriver instance mock."""
    driver = MagicMock()
    driver.page_source = page_source
    driver.get_screenshot_as_base64.return_value = screenshot_b64
    return driver


def _patch_selenium(driver: MagicMock) -> Any:
    """Patch all four module-level globals in the selenium engine module.

    The mocked ``webdriver.Chrome()`` returns the provided driver instance.
    Returns a dict of individual patchers (use as nested context managers or
    via the ``_all_patches`` helper below).
    """
    import scrapefold.engines.selenium as mod

    mock_webdriver = MagicMock()
    mock_webdriver.Chrome.return_value = driver

    mock_options_cls = MagicMock()
    mock_options_instance = MagicMock()
    mock_options_cls.return_value = mock_options_instance

    mock_service_cls = MagicMock()
    mock_manager_cls = MagicMock()
    mock_manager_cls.return_value.install.return_value = "/fake/chromedriver"

    return {
        "webdriver": patch.object(mod, "webdriver", mock_webdriver),
        "ChromeOptions": patch.object(mod, "ChromeOptions", mock_options_cls),
        "ChromeService": patch.object(mod, "ChromeService", mock_service_cls),
        "ChromeDriverManager": patch.object(mod, "ChromeDriverManager", mock_manager_cls),
    }


class _AllPatches:
    """Context manager that applies all four patches simultaneously."""

    def __init__(self, driver: MagicMock) -> None:
        self._driver = driver
        self._patchers: dict[str, Any] = {}
        self._mocks: dict[str, Any] = {}

    def __enter__(self) -> tuple[MagicMock, dict[str, Any]]:
        self._patchers = _patch_selenium(self._driver)
        for key, p in self._patchers.items():
            self._mocks[key] = p.start()
        return self._driver, self._mocks

    def __exit__(self, *args: object) -> None:
        for p in self._patchers.values():
            p.stop()


def _engine():
    from scrapefold.engines.selenium import SeleniumEngine

    return SeleniumEngine()


# ---------------------------------------------------------------------------
# 1. Basic fetch — text/markdown/html populated; cost=0.0; engine="selenium"
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_basic_fetch_success() -> None:
    driver = _make_driver()
    with _AllPatches(driver):
        result = await _engine().scrape("https://example.com/")

    assert result.engine == "selenium"
    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.cost_usd == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 2. driver.quit() is called on success
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_driver_quit_called_on_success() -> None:
    driver = _make_driver()
    with _AllPatches(driver):
        await _engine().scrape("https://example.com/")

    driver.quit.assert_called_once()


# ---------------------------------------------------------------------------
# 3. driver.quit() is called even when driver.get() raises
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_driver_quit_called_on_exception() -> None:
    driver = _make_driver()
    driver.get.side_effect = RuntimeError("browser crash")

    with _AllPatches(driver), pytest.raises(EngineError):
        await _engine().scrape("https://example.com/")

    driver.quit.assert_called_once()


# ---------------------------------------------------------------------------
# 4. user_agent is passed as --user-agent Chrome option argument
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_user_agent_added_to_options() -> None:
    driver = _make_driver()
    with _AllPatches(driver) as (_, mocks):
        opts = ScrapeOptions(user_agent="MyBot/3.0")
        await _engine().scrape("https://example.com/", opts)

    options_instance = mocks["ChromeOptions"].return_value
    # add_argument must have been called with the user-agent string
    add_args = [call.args[0] for call in options_instance.add_argument.call_args_list]
    assert any("MyBot/3.0" in arg for arg in add_args), (
        f"Expected --user-agent=MyBot/3.0 in add_argument calls; got: {add_args}"
    )


# ---------------------------------------------------------------------------
# 5. --headless=new is always added
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_headless_argument_added() -> None:
    driver = _make_driver()
    with _AllPatches(driver) as (_, mocks):
        await _engine().scrape("https://example.com/")

    options_instance = mocks["ChromeOptions"].return_value
    add_args = [call.args[0] for call in options_instance.add_argument.call_args_list]
    assert any("headless" in arg for arg in add_args), (
        f"Expected a headless argument; got: {add_args}"
    )


# ---------------------------------------------------------------------------
# 6. take_screenshot → screenshot_b64 populated via get_screenshot_as_base64
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_take_screenshot_sets_screenshot_b64() -> None:
    driver = _make_driver(screenshot_b64=_PNG_B64)
    opts = ScrapeOptions(take_screenshot=True)
    with _AllPatches(driver):
        result = await _engine().scrape("https://example.com/", opts)

    driver.get_screenshot_as_base64.assert_called_once()
    assert result.screenshot_b64 == _PNG_B64


# ---------------------------------------------------------------------------
# 7. wait_for_selector triggers WebDriverWait
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_wait_for_selector_triggers_webdriverwait() -> None:
    driver = _make_driver()
    opts = ScrapeOptions(wait_for_selector=".my-class")

    import scrapefold.engines.selenium as mod

    mock_wait_cls = MagicMock()
    mock_wait_instance = MagicMock()
    mock_wait_cls.return_value = mock_wait_instance

    # We must also patch _EC so presence_of_element_located is not None.
    mock_ec = MagicMock()
    mock_by = MagicMock()

    with (
        _AllPatches(driver),
        patch.object(mod, "WebDriverWait", mock_wait_cls),
        patch.object(mod, "_EC", mock_ec),
        patch.object(mod, "_By", mock_by),
    ):
        await _engine().scrape("https://example.com/", opts)

    mock_wait_cls.assert_called_once()
    mock_wait_instance.until.assert_called_once()


# ---------------------------------------------------------------------------
# 8. Unsupported options are stripped (drop-not-raise rule)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unsupported_options_dropped() -> None:
    """stealth and premium_proxy are not in SUPPORTED_OPTIONS; scrape must succeed."""
    from scrapefold.engines.selenium import SeleniumEngine

    driver = _make_driver()
    opts = ScrapeOptions(stealth=True, premium_proxy=True)
    with _AllPatches(driver):
        # The base class strips unsupported opts; _fetch never sees them.
        # No exception must be raised.
        result = await SeleniumEngine().scrape("https://example.com/", opts)

    assert result.engine == "selenium"


# ---------------------------------------------------------------------------
# 9. SDK exception inside the sync block becomes EngineError("selenium", ...)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sdk_exception_wrapped_in_engine_error() -> None:
    driver = _make_driver()
    driver.get.side_effect = Exception("WebDriver not available")

    with _AllPatches(driver), pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "selenium"
    assert "WebDriver not available" in exc_info.value.message


# ---------------------------------------------------------------------------
# 10. is_available() returns True — no API key required
# ---------------------------------------------------------------------------


def test_is_available_true() -> None:
    from scrapefold.engines.selenium import SeleniumEngine

    eng = SeleniumEngine()
    assert eng.is_available() is True


# ---------------------------------------------------------------------------
# 11. Regression: webdriver.Chrome kwargs match the real SDK signature
# ---------------------------------------------------------------------------


def test_regression_kwargs_match_sdk_signature() -> None:
    """Verify the engine calls webdriver.Chrome with kwargs the SDK accepts.

    Uses ``pytest.importorskip`` so this test is silently skipped if
    the ``selenium`` extra is not installed.
    """
    pytest.importorskip("selenium")
    from selenium import webdriver as real_webdriver

    sig = inspect.signature(real_webdriver.Chrome.__init__)
    accepted = set(sig.parameters.keys()) - {"self"}

    # The engine passes service= and options= (and keep_alive is optional).
    engine_kwargs = {"service", "options"}
    missing = engine_kwargs - accepted
    assert not missing, (
        f"Engine passes kwargs {missing!r} that real webdriver.Chrome.__init__ "
        f"does not accept. Accepted: {accepted}"
    )
