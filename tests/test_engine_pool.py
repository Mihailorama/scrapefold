"""Tests for EnginePool — engine instance caching across multiple walks."""

from __future__ import annotations

from typing import Any

import pytest

from scrapefold.engines import _REGISTRY
from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.pool import EnginePool


def _make_stub_engine(name: str, *, ctor_log: list[str]) -> type[ScrapeEngine]:
    class _Stub(ScrapeEngine):
        NAME = name
        CAPABILITIES = EngineCapabilities()
        SUPPORTED_OPTIONS = frozenset()

        def __init__(self) -> None:
            super().__init__()
            ctor_log.append(name)

        async def _fetch(self, url: str, opts: Any) -> Any:
            from scrapefold.result import ScrapeResult

            return ScrapeResult(
                url=url,
                text="x",
                markdown="# x",
                html=None,
                engine=self.NAME,
                elapsed_ms=1,
            )

    return _Stub


async def test_pool_caches_engine_across_get_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctor_log: list[str] = []
    cls = _make_stub_engine("pooltest", ctor_log=ctor_log)
    monkeypatch.setitem(_REGISTRY, "pooltest", lambda: cls)

    pool = EnginePool()
    e1 = pool.get("pooltest")
    e2 = pool.get("pooltest")

    assert e1 is e2
    assert ctor_log == ["pooltest"]  # constructor called exactly once


async def test_pool_aclose_closes_all_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class _Stub(ScrapeEngine):
        NAME = "p_close"
        CAPABILITIES = EngineCapabilities()
        SUPPORTED_OPTIONS = frozenset()

        async def _fetch(self, url: str, opts: Any) -> Any:
            from scrapefold.result import ScrapeResult

            return ScrapeResult(
                url=url,
                text="x",
                markdown="# x",
                html=None,
                engine=self.NAME,
                elapsed_ms=1,
            )

        async def aclose(self) -> None:
            closed.append(self.NAME)

    monkeypatch.setitem(_REGISTRY, "p_close", lambda: _Stub)

    pool = EnginePool()
    pool.get("p_close")
    await pool.aclose()

    assert closed == ["p_close"]
    # Idempotent close
    await pool.aclose()
    assert closed == ["p_close"]


async def test_pool_get_unknown_engine_raises_keyerror() -> None:
    pool = EnginePool()
    with pytest.raises(KeyError):
        pool.get("does-not-exist")


async def test_pool_aclose_propagates_to_engine_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pool closes, each engine's aclose() must release its
    httpx.AsyncClient / SDK client reference."""
    from scrapefold.engines import _REGISTRY
    from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
    from scrapefold.pool import EnginePool

    state: dict[str, Any] = {"client": object(), "closed": False}

    class _ClientEngine(ScrapeEngine):
        NAME = "client_engine"
        CAPABILITIES = EngineCapabilities()
        SUPPORTED_OPTIONS = frozenset()

        def __init__(self) -> None:
            super().__init__()
            self._client = state["client"]

        async def _fetch(self, url: str, opts: Any) -> Any:
            from scrapefold.result import ScrapeResult

            return ScrapeResult(
                url=url,
                text="x",
                markdown="# x",
                html=None,
                engine=self.NAME,
                elapsed_ms=1,
            )

        async def aclose(self) -> None:
            self._client = None
            state["closed"] = True

    monkeypatch.setitem(_REGISTRY, "client_engine", lambda: _ClientEngine)

    pool = EnginePool()
    engine = pool.get("client_engine")
    assert engine._client is state["client"]  # type: ignore[attr-defined]
    await pool.aclose()
    assert state["closed"] is True
    assert engine._client is None  # type: ignore[attr-defined]
