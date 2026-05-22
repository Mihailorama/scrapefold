"""Tests for the ScrapeEngine ABC contract — opt-stripping, timing, errors.

These run without any real engine installed, using a tiny in-test
subclass that does no I/O.
"""

from __future__ import annotations

import logging

import pytest

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult


class _StubEngine(ScrapeEngine):
    NAME = "stub"
    CAPABILITIES = EngineCapabilities(requires_api_key=False)
    SUPPORTED_OPTIONS = frozenset({"language", "render_js", "timeout_s"})

    def __init__(self, raise_with: str | None = None) -> None:
        super().__init__(api_key=None)
        self.raise_with = raise_with
        self.last_opts: ScrapeOptions | None = None

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        self.last_opts = opts
        if self.raise_with:
            raise RuntimeError(self.raise_with)
        return ScrapeResult(
            url=url, text="ok", markdown="ok", html=None, engine=self.NAME, elapsed_ms=0
        )


async def test_strips_unsupported_options_to_defaults(caplog: pytest.LogCaptureFixture) -> None:
    eng = _StubEngine()
    opts = ScrapeOptions(
        language="ru",
        country="ru",  # unsupported, should be reset to None
        render_js=False,
        stealth=True,  # unsupported, should be reset to False
        wait_ms=9000,  # unsupported, should be reset to 5000
    )
    with caplog.at_level(logging.DEBUG, logger="scrapefold.engines.base"):
        await eng.scrape("https://example.com", opts)
    assert eng.last_opts is not None
    # supported fields kept
    assert eng.last_opts.language == "ru"
    assert eng.last_opts.render_js is False
    # unsupported fields reset to defaults
    assert eng.last_opts.country is None
    assert eng.last_opts.stealth is False
    assert eng.last_opts.wait_ms == 5000
    # debug log mentions the dropped fields
    dropped = [r.message for r in caplog.records if "dropping unsupported" in r.message]
    assert any("country" in m for m in dropped)
    assert any("stealth" in m for m in dropped)


async def test_supports_all_when_supported_options_empty() -> None:
    class _Permissive(_StubEngine):
        SUPPORTED_OPTIONS = frozenset()

    eng = _Permissive()
    opts = ScrapeOptions(language="ru", country="ru", stealth=True)
    await eng.scrape("https://example.com", opts)
    assert eng.last_opts is not None
    assert eng.last_opts.language == "ru"
    assert eng.last_opts.country == "ru"
    assert eng.last_opts.stealth is True


async def test_elapsed_ms_is_populated() -> None:
    eng = _StubEngine()
    res = await eng.scrape("https://example.com")
    assert res.elapsed_ms >= 0


async def test_engine_error_wraps_underlying_exception() -> None:
    eng = _StubEngine(raise_with="boom")
    with pytest.raises(EngineError) as exc_info:
        await eng.scrape("https://example.com")
    assert exc_info.value.engine == "stub"
    assert "boom" in exc_info.value.message


def test_is_available_requires_api_key_by_default() -> None:
    class _Paid(_StubEngine):
        CAPABILITIES = EngineCapabilities(requires_api_key=True)

    assert _Paid().is_available() is False
    paid_with_key = _Paid()
    paid_with_key.api_key = "fc-abc"
    assert paid_with_key.is_available() is True
