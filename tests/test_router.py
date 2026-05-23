"""Tests for ``scrapefold.router.walk``.

All tests stub the engine registry via ``monkeypatch`` so no real network
calls are made. Follows the offline-by-default golden rule.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

import scrapefold
from scrapefold import (
    AllEnginesFailed,
    Policy,
    ScrapeOptions,
    ScrapeResult,
    SequentialStep,
)
from scrapefold.engines import _REGISTRY
from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.ladders import LADDERS, RaceStep

# ---------------------------------------------------------------------------
# Stub engines
# ---------------------------------------------------------------------------


def _make_result(
    engine_name: str, url: str = "https://example.com/", text: str = "hello"
) -> ScrapeResult:
    return ScrapeResult(
        url=url,
        text=text,
        markdown=f"# {text}",
        html=f"<h1>{text}</h1>",
        engine=engine_name,
        elapsed_ms=10,
    )


def _empty_result(engine_name: str, url: str) -> ScrapeResult:
    return ScrapeResult(url=url, text="", markdown="", html=None, engine=engine_name, elapsed_ms=1)


def _stub_engine(
    name: str,
    behavior: str = "good",
    *,
    requires_api_key: bool = False,
    on_call: Callable[[], None] | None = None,
) -> type[ScrapeEngine]:
    """Build a one-off stub engine class.

    ``behavior`` is one of ``"good"`` (non-empty result), ``"empty"`` (``is_empty()``
    result), ``"raise"`` (raises ``RuntimeError`` which the base wraps as
    ``EngineError``). ``on_call`` is a side-effect hook invoked before returning.
    """

    async def _fetch(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        if on_call is not None:
            on_call()
        if behavior == "raise":
            raise RuntimeError("boom")
        if behavior == "empty":
            return _empty_result(name, url)
        return _make_result(name, url=url, text="hello good")

    return type(
        f"_Stub_{name}",
        (ScrapeEngine,),
        {
            "NAME": name,
            "CAPABILITIES": EngineCapabilities(requires_api_key=requires_api_key),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch,
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, type[ScrapeEngine]]]:
    """Register stub engines in the lazy registry; auto-cleanup via monkeypatch."""
    stubs: dict[str, type[ScrapeEngine]] = {
        "stub_good": _stub_engine("stub_good", "good"),
        "stub_empty": _stub_engine("stub_empty", "empty"),
        "stub_raise": _stub_engine("stub_raise", "raise"),
        "stub_unavailable": _stub_engine("stub_unavailable", "good", requires_api_key=True),
    }
    for name, cls in stubs.items():
        monkeypatch.setitem(_REGISTRY, name, lambda cls=cls: cls)
    yield stubs


@pytest.fixture
def stub_ladder(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Any:
    """Override ``LADDERS['static_general']`` for the duration of a test.

    Usage: ``@pytest.mark.parametrize('stub_ladder', [(SequentialStep('stub_good'),)], indirect=True)``
    or call ``set_ladder(steps)`` from inside the test.
    """

    def _set(steps: tuple[Any, ...]) -> None:
        monkeypatch.setitem(LADDERS, "static_general", steps)

    return _set


# ---------------------------------------------------------------------------
# 1. Happy path — single SequentialStep returns the first good result
# ---------------------------------------------------------------------------


async def test_router_returns_first_good_result(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder((SequentialStep(engine="stub_good"),))

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"
    assert result.text == "hello good"
    assert result.url == "https://example.com/"


# ---------------------------------------------------------------------------
# 2. Empty result advances to next step
# ---------------------------------------------------------------------------


async def test_router_advances_on_empty_result(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_empty"),
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 3. EngineError advances to next step
# ---------------------------------------------------------------------------


async def test_router_advances_on_engine_error(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_raise"),
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 4. Unavailable engine (is_available False) is skipped
# ---------------------------------------------------------------------------


async def test_router_skips_unavailable_engine(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_unavailable"),
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 5. All steps fail → AllEnginesFailed
# ---------------------------------------------------------------------------


async def test_router_raises_when_all_engines_fail(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_empty"),
            SequentialStep(engine="stub_raise"),
        )
    )

    with pytest.raises(AllEnginesFailed):
        await walk("https://example.com/")


# ---------------------------------------------------------------------------
# 6. Unknown engine name is skipped without crashing the walk
# ---------------------------------------------------------------------------


async def test_router_skips_unknown_engine(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="does_not_exist_anywhere"),
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 7. Policy gating — paid engines blocked when policy.paid_allowed=False
# ---------------------------------------------------------------------------


async def test_router_respects_paid_allowed_false(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_good", estimated_cost_usd=0.01),  # paid
            SequentialStep(engine="stub_good", estimated_cost_usd=0.0),  # free
        )
    )

    opts = ScrapeOptions(extra={"policy": Policy(paid_allowed=False)})
    result = await walk("https://example.com/", opts)

    # Paid step skipped → falls through to free step.
    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 8. Budget — engines_count ceiling halts the walk
# ---------------------------------------------------------------------------


async def test_router_halts_when_engine_budget_exhausted(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scrapefold.router import walk

    monkeypatch.setitem(_REGISTRY, "stub_empty2", lambda: _stub_engine("stub_empty2", "empty"))

    # Two distinct empty engines then a good one; max_engines=2 means the good
    # step exceeds the engine-count ceiling and the walk halts before invoking it.
    stub_ladder(
        (
            SequentialStep(engine="stub_empty"),
            SequentialStep(engine="stub_empty2"),
            SequentialStep(engine="stub_good"),
        )
    )

    opts = ScrapeOptions(extra={"max_engines": 2})
    with pytest.raises(AllEnginesFailed):
        await walk("https://example.com/", opts)


# ---------------------------------------------------------------------------
# 9. RaceStep in the ladder is currently skipped (TBD), walk falls through
# ---------------------------------------------------------------------------


async def test_router_skips_race_step_for_now(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            RaceStep(engines=("stub_good", "stub_empty")),  # not yet implemented
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    # RaceStep skipped → SequentialStep wins.
    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 10. Public scrapefold.scrape() delegates to the router
# ---------------------------------------------------------------------------


async def test_public_scrape_delegates_to_router(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    stub_ladder((SequentialStep(engine="stub_good"),))

    result = await scrapefold.scrape("https://example.com/")

    assert result.engine == "stub_good"


# ---------------------------------------------------------------------------
# 11. Failures list records what was tried before the winner
# ---------------------------------------------------------------------------


async def test_router_records_failures_in_result(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_raise"),
            SequentialStep(engine="stub_empty"),
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"
    # The two prior failures must be visible to the caller.
    joined = " ".join(result.failures)
    assert "stub_raise" in joined
    assert "stub_empty" in joined


# ---------------------------------------------------------------------------
# 12. Engine that was already tried is not re-attempted within the same walk
# ---------------------------------------------------------------------------


async def test_router_does_not_retry_same_engine_within_walk(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scrapefold.router import walk

    call_count = {"n": 0}

    def _bump() -> None:
        call_count["n"] += 1

    monkeypatch.setitem(
        _REGISTRY, "stub_counting", lambda: _stub_engine("stub_counting", "empty", on_call=_bump)
    )
    stub_ladder(
        (
            SequentialStep(engine="stub_counting"),
            SequentialStep(engine="stub_counting"),  # same engine again
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    assert result.engine == "stub_good"
    assert call_count["n"] == 1, "engine must not be invoked twice within the same walk"


# ---------------------------------------------------------------------------
# 13. EngineError is wrapped; the walk advances rather than propagating it
# ---------------------------------------------------------------------------


async def test_router_does_not_propagate_engine_error(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_raise"),
            SequentialStep(engine="stub_good"),
        )
    )

    # EngineError must not bubble up — the walk should catch and continue.
    try:
        result = await walk("https://example.com/")
    except EngineError:
        pytest.fail("router must not propagate EngineError; it should advance to next step")
    assert result.engine == "stub_good"
