"""Tests for the public search() fan-out + fusion entrypoint.

The search registry is monkeypatched with in-memory fake engines, so no HTTP
happens and the test exercises only the orchestration + fusion wiring.
"""

from __future__ import annotations

import pytest

from scrapefold.search import engines as engines_mod
from scrapefold.search.api import search
from scrapefold.search.engines.base import SearchEngine, SearchEngineError
from scrapefold.search.options import SearchOptions
from scrapefold.search.types import SearchHit


class _FakeA(SearchEngine):
    NAME = "fake_a"
    REQUIRES_API_KEY = False

    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        return [SearchHit(url="https://one.com"), SearchHit(url="https://two.com")]


class _FakeB(SearchEngine):
    NAME = "fake_b"
    REQUIRES_API_KEY = False

    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        return [SearchHit(url="https://two.com"), SearchHit(url="https://three.com")]


class _FakeBoom(SearchEngine):
    NAME = "fake_boom"
    REQUIRES_API_KEY = False

    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        raise RuntimeError("boom")


def _patch_registry(monkeypatch: pytest.MonkeyPatch, mapping: dict) -> None:
    monkeypatch.setattr(engines_mod, "_REGISTRY", mapping)


async def test_fans_out_and_fuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, {"fake_a": lambda: _FakeA, "fake_b": lambda: _FakeB})

    results = await search("q")

    # two.com is returned by both engines -> ranks first via RRF consensus.
    assert [r.url for r in results] == ["https://two.com", "https://one.com", "https://three.com"]
    assert results[0].consensus == 2
    assert set(results[0].score_breakdown) == {"fake_a", "fake_b"}


async def test_count_truncates_fused_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, {"fake_a": lambda: _FakeA, "fake_b": lambda: _FakeB})

    results = await search("q", SearchOptions(count=2))

    assert len(results) == 2
    assert results[0].url == "https://two.com"


async def test_one_engine_failing_still_yields_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, {"fake_a": lambda: _FakeA, "fake_boom": lambda: _FakeBoom})

    results = await search("q")

    assert {r.url for r in results} == {"https://one.com", "https://two.com"}
    assert all(r.engines == ("fake_a",) for r in results)


async def test_all_engines_failing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, {"fake_boom": lambda: _FakeBoom})

    with pytest.raises(SearchEngineError) as exc_info:
        await search("q")

    assert exc_info.value.engine == "search"


async def test_explicit_engines_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, {"fake_a": lambda: _FakeA, "fake_b": lambda: _FakeB})

    results = await search("q", SearchOptions(engines=("fake_a",)))

    # only fake_a queried -> its two urls, no fake_b-only "three.com"
    assert {r.url for r in results} == {"https://one.com", "https://two.com"}
