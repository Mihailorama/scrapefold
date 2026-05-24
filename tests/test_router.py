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


_GOOD_TEXT = (
    "This is a well-formed scraped page. " * 10
)  # 360 chars — well above is_suspicious threshold


def _make_result(
    engine_name: str, url: str = "https://example.com/", text: str = _GOOD_TEXT
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
        return _make_result(name, url=url)

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
    assert result.text == _GOOD_TEXT
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """paid_allowed=False must block engines with requires_api_key=True.

    NOTE: The gate checks engine CAPABILITIES.requires_api_key, not the step's
    estimated_cost_usd.  A SequentialStep with estimated_cost_usd>0 but a free
    engine (requires_api_key=False) is NOT blocked — the step metadata is not
    the authority; only the engine capability is.
    """
    from scrapefold.router import walk

    called: list[str] = []

    def _paid_call() -> None:
        called.append("paid_engine")

    paid_engine = _stub_engine("stub_paid_gate", "good", requires_api_key=True, on_call=_paid_call)
    free_engine = _stub_engine("stub_free_gate", "good", requires_api_key=False)

    monkeypatch.setitem(_REGISTRY, "stub_paid_gate", lambda: paid_engine)
    monkeypatch.setitem(_REGISTRY, "stub_free_gate", lambda: free_engine)

    stub_ladder(
        (
            SequentialStep(engine="stub_paid_gate"),
            SequentialStep(engine="stub_free_gate"),
        )
    )

    opts = ScrapeOptions(extra={"policy": Policy(paid_allowed=False)})
    result = await walk("https://example.com/", opts)

    # Paid engine (requires_api_key=True) must not have been called.
    assert called == [], "paid engine must not be called when paid_allowed=False"
    # Free engine wins.
    assert result.engine == "stub_free_gate"
    # Failures record the skip reason.
    assert any("stub_paid_gate" in f and "paid_not_allowed" in f for f in result.failures)


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
# 9. RaceStep in the ladder is walked sequentially in v0.1
# ---------------------------------------------------------------------------


async def test_walk_walks_race_step_members_sequentially(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RaceStep members must be tried one-by-one (sequential within race) in v0.1.

    Build a ladder with a single RaceStep containing 3 engines.  The first two
    return empty; the third returns success.  All three must be invoked.
    """
    from scrapefold.router import walk

    call_log: list[str] = []

    def _make_logging_engine(name: str, behavior: str) -> type[ScrapeEngine]:
        def _on_call() -> None:
            call_log.append(name)

        return _stub_engine(name, behavior, on_call=_on_call)

    monkeypatch.setitem(
        _REGISTRY, "race_empty1", lambda: _make_logging_engine("race_empty1", "empty")
    )
    monkeypatch.setitem(
        _REGISTRY, "race_empty2", lambda: _make_logging_engine("race_empty2", "empty")
    )
    monkeypatch.setitem(_REGISTRY, "race_good3", lambda: _make_logging_engine("race_good3", "good"))

    stub_ladder((RaceStep(engines=("race_empty1", "race_empty2", "race_good3")),))

    result = await walk("https://example.com/")

    # All three engines in the race must have been called in order.
    assert call_log == ["race_empty1", "race_empty2", "race_good3"], (
        f"expected all 3 race members called in order; got {call_log}"
    )
    assert result.engine == "race_good3"
    # The two failed engines appear in failures.
    joined = " ".join(result.failures)
    assert "race_empty1" in joined
    assert "race_empty2" in joined


async def test_walk_race_step_short_circuits_on_first_good(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once a RaceStep member returns a good result the remaining members are skipped."""
    from scrapefold.router import walk

    call_log: list[str] = []

    def _make_logging_engine(name: str, behavior: str) -> type[ScrapeEngine]:
        def _on_call() -> None:
            call_log.append(name)

        return _stub_engine(name, behavior, on_call=_on_call)

    monkeypatch.setitem(
        _REGISTRY, "race_first_good", lambda: _make_logging_engine("race_first_good", "good")
    )
    monkeypatch.setitem(
        _REGISTRY, "race_should_skip", lambda: _make_logging_engine("race_should_skip", "good")
    )

    stub_ladder((RaceStep(engines=("race_first_good", "race_should_skip")),))

    result = await walk("https://example.com/")

    assert result.engine == "race_first_good"
    assert "race_should_skip" not in call_log, (
        "second race member must not be called once first returns a good result"
    )


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


# ---------------------------------------------------------------------------
# 14. AllEnginesFailed carries .url and .failures for consumer introspection
# ---------------------------------------------------------------------------


async def test_all_engines_failed_carries_url_and_failures(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    """AllEnginesFailed exposes .url and .failures for consumer introspection."""
    from scrapefold.router import walk

    stub_ladder(
        (
            SequentialStep(engine="stub_empty"),
            SequentialStep(engine="stub_raise"),
        )
    )

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/probe")

    assert exc_info.value.url == "https://example.com/probe"
    assert isinstance(exc_info.value.failures, list)
    assert any("stub_empty" in f for f in exc_info.value.failures)
    assert any("stub_raise" in f for f in exc_info.value.failures)


# ---------------------------------------------------------------------------
# 15. Suspicious responses are not returned — router escalates past them
# ---------------------------------------------------------------------------


async def test_router_treats_suspicious_response_as_failure(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scrape that returns short text containing antibot phrases is suspicious;
    the router must advance to the next step rather than returning it."""
    from scrapefold import EngineCapabilities, SequentialStep
    from scrapefold.engines import _REGISTRY
    from scrapefold.engines.base import ScrapeEngine
    from scrapefold.result import ScrapeResult
    from scrapefold.router import walk

    async def _fetch_captcha(self, url, opts):
        # Short text containing a known antibot phrase
        return ScrapeResult(
            url=url,
            text="Just a moment...",
            markdown="Just a moment...",
            html=None,
            engine=self.NAME,
            elapsed_ms=1,
        )

    captcha_engine = type(
        "_StubCaptcha",
        (ScrapeEngine,),
        {
            "NAME": "stub_captcha",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_captcha,
        },
    )
    monkeypatch.setitem(_REGISTRY, "stub_captcha", lambda: captcha_engine)

    stub_ladder(
        (
            SequentialStep(engine="stub_captcha"),
            SequentialStep(engine="stub_good"),
        )
    )

    result = await walk("https://example.com/")

    # Suspicious captcha skipped → stub_good wins.
    assert result.engine == "stub_good"
    assert any("stub_captcha:suspicious" in f for f in result.failures)


# ---------------------------------------------------------------------------
# 16. opts.engines override — only named engines are tried, in order
# ---------------------------------------------------------------------------


async def test_walk_honors_opts_engines_override(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.engines=("X","Y") MUST drive the walk, ignoring the default ladder."""
    from scrapefold.router import walk

    call_log: list[str] = []

    # Two engines that record when called; first is empty, second is good.
    def _empty_call() -> None:
        call_log.append("override_empty")

    def _good_call() -> None:
        call_log.append("override_good")

    monkeypatch.setitem(
        _REGISTRY,
        "override_empty",
        lambda: _stub_engine("override_empty", "empty", on_call=_empty_call),
    )
    monkeypatch.setitem(
        _REGISTRY,
        "override_good",
        lambda: _stub_engine("override_good", "good", on_call=_good_call),
    )

    # The default ladder uses stub_good — but it must NOT be called.
    stub_ladder((SequentialStep(engine="stub_good"),))

    opts = ScrapeOptions(engines=("override_empty", "override_good"))
    result = await walk("https://example.com/override", opts)

    assert result.engine == "override_good"
    assert result.url == "https://example.com/override"
    # Both override engines were called in order; stub_good (ladder) was NOT.
    assert call_log == ["override_empty", "override_good"]
    # Empty engine appears in failures list.
    assert any("override_empty" in f for f in result.failures)


async def test_walk_opts_engines_empty_tuple_uses_ladder(
    stub_registry: dict[str, type[ScrapeEngine]],
    stub_ladder: Any,
) -> None:
    """Empty tuple in opts.engines falls back to the default ladder (None semantics)."""
    from scrapefold.router import walk

    stub_ladder((SequentialStep(engine="stub_good"),))

    opts = ScrapeOptions(engines=())
    result = await walk("https://example.com/", opts)

    assert result.engine == "stub_good"


async def test_walk_opts_engines_unknown_name_skipped(
    stub_registry: dict[str, type[ScrapeEngine]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown engine name in opts.engines records :unknown and continues to next."""
    from scrapefold.router import walk

    monkeypatch.setitem(
        _REGISTRY,
        "known_good",
        lambda: _stub_engine("known_good", "good"),
    )

    opts = ScrapeOptions(engines=("does_not_exist_xyz", "known_good"))
    result = await walk("https://example.com/", opts)

    assert result.engine == "known_good"
    assert any("does_not_exist_xyz:unknown" in f for f in result.failures)


async def test_walk_opts_engines_all_fail_raises(
    stub_registry: dict[str, type[ScrapeEngine]],
) -> None:
    """When all engines in opts.engines fail, AllEnginesFailed is raised."""
    from scrapefold.router import walk

    opts = ScrapeOptions(engines=("stub_empty", "stub_raise"))
    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/probe", opts)

    assert exc_info.value.url == "https://example.com/probe"
    assert any("stub_empty" in f for f in exc_info.value.failures)
    assert any("stub_raise" in f for f in exc_info.value.failures)


# ---------------------------------------------------------------------------
# 20. opts.engines override — policy gate blocks paid engines
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 21. opts.engines override — max_cost_usd=0 blocks a paid engine pre-call
# ---------------------------------------------------------------------------


async def test_walk_opts_engines_respects_max_cost_usd_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.engines + max_cost_usd=0 MUST NOT invoke a paid engine.

    The engine has estimated_cost_usd > 0; cost_accum starts at 0.
    pre-check: 0 + cost > 0 → skip before scrape() is ever called.
    """
    from scrapefold.router import walk

    paid_called: list[bool] = []

    def _paid_call() -> None:
        paid_called.append(True)

    paid_engine = type(
        "_StubPaidCost",
        (ScrapeEngine,),
        {
            "NAME": "stub_paid_cost",
            "CAPABILITIES": EngineCapabilities(requires_api_key=True, estimated_cost_usd=0.001),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": lambda self, url, opts: (_ for _ in ()).throw(
                AssertionError("must not call")
            ),
        },
    )

    async def _fetch_paid(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        _paid_call()
        return _make_result("stub_paid_cost", url=url)

    paid_engine._fetch = _fetch_paid  # type: ignore[attr-defined]

    monkeypatch.setitem(_REGISTRY, "stub_paid_cost", lambda: paid_engine)

    # paid_allowed=True so the policy gate passes — only the cost gate should block it.
    opts = ScrapeOptions(
        engines=("stub_paid_cost",),
        extra={"max_cost_usd": 0.0, "policy": Policy(paid_allowed=True)},
    )

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    # The engine must never have been invoked.
    assert paid_called == [], "paid engine must not be called when max_cost_usd=0"
    # Failures should record the budget skip.
    assert any("budget:cost" in f for f in exc_info.value.failures)


# ---------------------------------------------------------------------------
# 22. opts.engines override — max_engines cap halts the walk
# ---------------------------------------------------------------------------


async def test_walk_opts_engines_respects_max_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.engines + max_engines=1 MUST stop after the first attempt."""
    from scrapefold.router import walk

    call_log: list[str] = []

    def _call1() -> None:
        call_log.append("empty1")

    def _call2() -> None:
        call_log.append("empty2")

    monkeypatch.setitem(
        _REGISTRY, "me_empty1", lambda: _stub_engine("me_empty1", "empty", on_call=_call1)
    )
    monkeypatch.setitem(
        _REGISTRY, "me_empty2", lambda: _stub_engine("me_empty2", "empty", on_call=_call2)
    )

    opts = ScrapeOptions(engines=("me_empty1", "me_empty2"), extra={"max_engines": 1})

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    # Only the first engine must have been invoked.
    assert call_log == ["empty1"], "second engine must not be called when max_engines=1"
    failures = exc_info.value.failures
    assert any("me_empty1" in f for f in failures), "first engine failure must be recorded"
    assert any("budget:max_engines" in f for f in failures), (
        "budget:max_engines must appear in failures"
    )


# ---------------------------------------------------------------------------
# 23. opts.engines override — elapsed-time budget halts the walk
# ---------------------------------------------------------------------------


async def test_walk_opts_engines_respects_timeout_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.engines + accumulated elapsed_ms > timeout_s*1000 MUST halt the walk."""
    from scrapefold.router import walk

    call_log: list[str] = []

    async def _fetch_slow(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        call_log.append(self.NAME)
        # Return a result whose elapsed_ms far exceeds the per-walk timeout budget.
        return ScrapeResult(
            url=url,
            text="",
            markdown="",
            html=None,
            engine=self.NAME,
            elapsed_ms=200_000,  # 200 s on a 60 s timeout → blows budget
        )

    slow_engine = type(
        "_StubSlow",
        (ScrapeEngine,),
        {
            "NAME": "tb_slow",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_slow,
        },
    )

    async def _fetch_fast(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        call_log.append(self.NAME)
        return ScrapeResult(
            url=url,
            text=_GOOD_TEXT,
            markdown=f"# {_GOOD_TEXT}",
            html=None,
            engine=self.NAME,
            elapsed_ms=1,
        )

    fast_engine = type(
        "_StubFast",
        (ScrapeEngine,),
        {
            "NAME": "tb_fast",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_fast,
        },
    )

    monkeypatch.setitem(_REGISTRY, "tb_slow", lambda: slow_engine)
    monkeypatch.setitem(_REGISTRY, "tb_fast", lambda: fast_engine)

    # timeout_s=60 → budget = 60 000 ms; slow engine burns 200 000 ms.
    opts = ScrapeOptions(engines=("tb_slow", "tb_fast"), timeout_s=60)

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    # Slow engine was called (empty result), fast engine must NOT be called.
    assert "tb_slow" in call_log
    assert "tb_fast" not in call_log, (
        "second engine must not be called after timeout budget exceeded"
    )
    failures = exc_info.value.failures
    assert any("budget:timeout" in f for f in failures), "budget:timeout must appear in failures"


async def test_walk_opts_engines_dedupe_by_canonical_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate names in opts.engines invoke each engine at most once.

    Sequence: dedup_engine is tried first (fails empty), then the same name
    appears twice more in the list.  The engine must only be called once;
    both duplicate entries must appear in failures as ``<canonical>:duplicate``.
    A final good engine wins so the test can inspect result.failures.
    """
    from scrapefold.router import walk

    call_log: list[str] = []

    def _on_dup_call() -> None:
        call_log.append("dedup_engine")

    def _on_winner_call() -> None:
        call_log.append("dedup_winner")

    # dedup_engine: returns empty (so the walk continues past it).
    dedup_engine = _stub_engine("dedup_engine", "empty", on_call=_on_dup_call)

    # A second registry key that resolves to the SAME canonical NAME.
    dedup_alias = type(
        "_StubDedupAlias",
        (ScrapeEngine,),
        {
            "NAME": "dedup_engine",  # same canonical NAME
            "CAPABILITIES": EngineCapabilities(requires_api_key=False),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": dedup_engine._fetch,
        },
    )

    # A fresh good engine that wins after all duplicates are skipped.
    winner_engine = _stub_engine("dedup_winner", "good", on_call=_on_winner_call)

    monkeypatch.setitem(_REGISTRY, "dedup_engine", lambda: dedup_engine)
    monkeypatch.setitem(_REGISTRY, "dedup_alias", lambda: dedup_alias)
    monkeypatch.setitem(_REGISTRY, "dedup_winner", lambda: winner_engine)

    # "dedup_engine" appears first (called + fails empty), then twice more as
    # duplicates (skipped), then the winner closes out.
    opts = ScrapeOptions(engines=("dedup_engine", "dedup_engine", "dedup_alias", "dedup_winner"))
    result = await walk("https://example.com/", opts)

    # dedup_engine must have been called exactly once.
    assert call_log.count("dedup_engine") == 1, (
        f"dedup_engine must be invoked once; call_log={call_log}"
    )
    assert result.engine == "dedup_winner"
    # Both duplicate entries must appear in failures with :duplicate tag.
    dup_failures = [f for f in result.failures if "duplicate" in f]
    assert len(dup_failures) == 2, f"both duplicates should be recorded; failures={result.failures}"


async def test_walk_opts_engines_policy_blocks_paid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.engines with paid_allowed=False must NOT invoke a paid engine.

    The paid engine (requires_api_key=True) should appear in failures with
    a :skipped:paid_not_allowed tag; a subsequent free engine should succeed.
    """
    from scrapefold.router import walk

    paid_called: list[bool] = []

    def _paid_call() -> None:
        paid_called.append(True)

    paid_engine = _stub_engine("stub_paid", "good", requires_api_key=True, on_call=_paid_call)
    free_engine = _stub_engine("stub_free", "good", requires_api_key=False)

    monkeypatch.setitem(_REGISTRY, "stub_paid", lambda: paid_engine)
    monkeypatch.setitem(_REGISTRY, "stub_free", lambda: free_engine)

    opts = ScrapeOptions(
        engines=("stub_paid", "stub_free"),
        extra={"policy": Policy(paid_allowed=False)},
    )
    result = await walk("https://example.com/", opts)

    # Paid engine must never have been invoked.
    assert paid_called == [], "paid engine must not be called when paid_allowed=False"
    # Free engine wins.
    assert result.engine == "stub_free"
    # Failures record the skip reason.
    assert any("stub_paid" in f and "paid_not_allowed" in f for f in result.failures)


# ---------------------------------------------------------------------------
# 30. Default ladder — paid_allowed enforced for engines inside a RaceStep
# ---------------------------------------------------------------------------


async def test_walk_default_ladder_respects_paid_allowed_in_race_step(
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """paid_allowed=False must block paid engines that appear inside a RaceStep.

    Root cause of Codex P1 finding: the old code checked the RaceStep's own
    estimated_cost_usd (often 0.0) rather than each engine's CAPABILITIES, so
    paid members inside a race were silently allowed through.
    """
    from scrapefold.router import walk

    paid_called: list[bool] = []

    def _paid_call() -> None:
        paid_called.append(True)

    paid_engine = _stub_engine(
        "race_paid_engine", "good", requires_api_key=True, on_call=_paid_call
    )
    free_engine = _stub_engine("race_free_engine", "good", requires_api_key=False)

    monkeypatch.setitem(_REGISTRY, "race_paid_engine", lambda: paid_engine)
    monkeypatch.setitem(_REGISTRY, "race_free_engine", lambda: free_engine)

    # RaceStep with one paid + one free engine; the race step itself carries
    # estimated_cost_usd=0.0 (the default), which is the exact scenario where
    # the old code would bypass the gate.
    stub_ladder((RaceStep(engines=("race_paid_engine", "race_free_engine")),))

    opts = ScrapeOptions(extra={"policy": Policy(paid_allowed=False)})
    result = await walk("https://example.com/", opts)

    # The paid engine must never have been invoked.
    assert paid_called == [], (
        "paid engine inside RaceStep must not be called when paid_allowed=False"
    )
    # The free engine inside the same RaceStep must win.
    assert result.engine == "race_free_engine"
    assert any("race_paid_engine" in f and "paid_not_allowed" in f for f in result.failures)


# ---------------------------------------------------------------------------
# 31. Default ladder — max_cost_usd enforced for engines inside a RaceStep
# ---------------------------------------------------------------------------


async def test_walk_default_ladder_respects_max_cost_usd_in_race_step(
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_cost_usd=0 must block paid engines that appear inside a RaceStep.

    Root cause of Codex P1 finding: the old code used the RaceStep's
    estimated_cost_usd (0.0) for the budget check, so engines with
    non-zero CAPABILITIES.estimated_cost_usd inside the race were not blocked.
    """
    from scrapefold.router import walk

    paid_called: list[bool] = []

    def _paid_call() -> None:
        paid_called.append(True)

    async def _fetch_paid(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        _paid_call()
        return _make_result("race_costly_engine", url=url)

    costly_engine = type(
        "_StubRaceCostly",
        (ScrapeEngine,),
        {
            "NAME": "race_costly_engine",
            "CAPABILITIES": EngineCapabilities(requires_api_key=True, estimated_cost_usd=0.001),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_paid,
        },
    )

    monkeypatch.setitem(_REGISTRY, "race_costly_engine", lambda: costly_engine)

    # RaceStep with a paid engine; step-level estimated_cost_usd stays at 0.0.
    stub_ladder((RaceStep(engines=("race_costly_engine",)),))

    # paid_allowed=True so policy gate passes — only the cost gate should block it.
    opts = ScrapeOptions(extra={"max_cost_usd": 0.0, "policy": Policy(paid_allowed=True)})

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    assert paid_called == [], "paid engine inside RaceStep must not be called when max_cost_usd=0"
    assert any("budget:cost" in f for f in exc_info.value.failures)


# ---------------------------------------------------------------------------
# 32. Default ladder — dedup across SequentialStep and RaceStep
# ---------------------------------------------------------------------------


async def test_walk_default_ladder_dedup_across_steps(
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine appearing in both a SequentialStep and a RaceStep is called only once."""
    from scrapefold.router import walk

    call_count = {"n": 0}

    def _bump() -> None:
        call_count["n"] += 1

    shared_engine = _stub_engine("shared_dedup_engine", "empty", on_call=_bump)
    good_engine = _stub_engine("dedup_winner2", "good")

    monkeypatch.setitem(_REGISTRY, "shared_dedup_engine", lambda: shared_engine)
    monkeypatch.setitem(_REGISTRY, "dedup_winner2", lambda: good_engine)

    stub_ladder(
        (
            SequentialStep(engine="shared_dedup_engine"),
            RaceStep(engines=("shared_dedup_engine", "dedup_winner2")),
        )
    )

    result = await walk("https://example.com/")

    # shared_dedup_engine must only have been called once (from SequentialStep).
    assert call_count["n"] == 1, (
        f"engine must not be retried across steps; called {call_count['n']} times"
    )
    # dedup_winner2 (second member of the RaceStep) must win.
    assert result.engine == "dedup_winner2"


# ---------------------------------------------------------------------------
# 33. _attempt_engine credits cost on EngineError (budget tracking)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 34. Policy gate — legal_constraints_blocked blocks matching engine
# ---------------------------------------------------------------------------


async def test_walk_helper_respects_legal_constraints_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine with legal_constraints=('eu_gdpr',) must be skipped when
    Policy(legal_constraints_blocked={'eu_gdpr'}) is set.

    A subsequent unconstrained engine should succeed.
    """
    from scrapefold.router import walk

    constrained_engine = type(
        "_StubLegalConstrained",
        (ScrapeEngine,),
        {
            "NAME": "lc_constrained",
            "CAPABILITIES": EngineCapabilities(
                requires_api_key=False,
                legal_constraints=("eu_gdpr",),
            ),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": lambda self, url, opts: (_ for _ in ()).throw(
                AssertionError("constrained engine must not be called")
            ),
        },
    )
    free_engine = _stub_engine("lc_free", "good")

    monkeypatch.setitem(_REGISTRY, "lc_constrained", lambda: constrained_engine)
    monkeypatch.setitem(_REGISTRY, "lc_free", lambda: free_engine)

    opts = ScrapeOptions(
        engines=("lc_constrained", "lc_free"),
        extra={"policy": Policy(legal_constraints_blocked=frozenset({"eu_gdpr"}))},
    )
    result = await walk("https://example.com/", opts)

    assert result.engine == "lc_free"
    # The failure record must contain the skip reason with the constraint name.
    assert any(
        "lc_constrained" in f and "skipped:legal" in f and "eu_gdpr" in f for f in result.failures
    ), f"expected legal skip failure; got {result.failures}"


# ---------------------------------------------------------------------------
# 35. Policy gate — geography_required blocks engine without matching geo
# ---------------------------------------------------------------------------


async def test_walk_helper_respects_geography_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine with geography=('us',) must be skipped when
    Policy(geography_required='eu') is set (geo mismatch).

    An engine with geography=('eu',) should pass.
    """
    from scrapefold.router import walk

    us_engine = type(
        "_StubGeoUS",
        (ScrapeEngine,),
        {
            "NAME": "geo_us",
            "CAPABILITIES": EngineCapabilities(
                requires_api_key=False,
                geography=("us",),
            ),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": lambda self, url, opts: (_ for _ in ()).throw(
                AssertionError("us engine must not be called when eu is required")
            ),
        },
    )

    async def _fetch_eu(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        return _make_result("geo_eu", url=url)

    eu_engine = type(
        "_StubGeoEU",
        (ScrapeEngine,),
        {
            "NAME": "geo_eu",
            "CAPABILITIES": EngineCapabilities(
                requires_api_key=False,
                geography=("eu",),
            ),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_eu,
        },
    )

    monkeypatch.setitem(_REGISTRY, "geo_us", lambda: us_engine)
    monkeypatch.setitem(_REGISTRY, "geo_eu", lambda: eu_engine)

    opts = ScrapeOptions(
        engines=("geo_us", "geo_eu"),
        extra={"policy": Policy(geography_required="eu")},
    )
    result = await walk("https://example.com/", opts)

    assert result.engine == "geo_eu"
    # The us engine must appear in failures with the geography skip reason.
    assert any("geo_us" in f and "skipped:geography:eu" in f for f in result.failures), (
        f"expected geography skip failure; got {result.failures}"
    )


# ---------------------------------------------------------------------------
# 36. Unavailable engine does NOT consume max_engines slot
# ---------------------------------------------------------------------------


async def test_walk_unavailable_engine_does_not_consume_max_engines_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When X is unavailable and Y is available, max_engines=1 still allows Y.

    X is unavailable (requires_api_key=True, no api_key set), so it must not
    consume a slot.  Y must be invoked and win.  The failures list must contain
    X:unavailable but must NOT contain budget:max_engines.
    """
    from scrapefold.router import walk

    unavail_engine = _stub_engine("slot_unavail", "good", requires_api_key=True)
    avail_engine = _stub_engine("slot_avail", "good", requires_api_key=False)

    monkeypatch.setitem(_REGISTRY, "slot_unavail", lambda: unavail_engine)
    monkeypatch.setitem(_REGISTRY, "slot_avail", lambda: avail_engine)

    opts = ScrapeOptions(
        engines=("slot_unavail", "slot_avail"),
        extra={"max_engines": 1},
    )
    result = await walk("https://example.com/", opts)

    assert result.engine == "slot_avail", (
        "available engine must win when unavailable engine precedes it with max_engines=1"
    )
    assert any("slot_unavail:unavailable" in f for f in result.failures), (
        "unavailable engine must appear in failures"
    )
    assert not any("budget:max_engines" in f for f in result.failures), (
        "budget:max_engines must NOT appear; unavailable engine did not consume a slot"
    )


# ---------------------------------------------------------------------------
# 37. Unavailable engine deduped but not counted against budget
# ---------------------------------------------------------------------------


async def test_walk_unavailable_engine_deduped_but_not_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """opts.engines=('X','X') where X is unavailable — only one :unavailable
    failure is recorded (dedup); no budget:max_engines appears since the engine
    was never actually invoked.
    """
    from scrapefold.router import walk

    unavail_engine = _stub_engine("dedup_unavail", "good", requires_api_key=True)
    monkeypatch.setitem(_REGISTRY, "dedup_unavail", lambda: unavail_engine)

    opts = ScrapeOptions(
        engines=("dedup_unavail", "dedup_unavail"),
        extra={"max_engines": 1},
    )
    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    failures = exc_info.value.failures
    unavail_count = sum(1 for f in failures if "dedup_unavail:unavailable" in f)
    assert unavail_count == 1, (
        f"unavailable engine must appear exactly once (dedup); got count={unavail_count} "
        f"in {failures}"
    )
    assert not any("budget:max_engines" in f for f in failures), (
        "budget:max_engines must NOT appear; unavailable engine does not consume a slot"
    )


# ---------------------------------------------------------------------------
# 33 (budget). _attempt_engine credits cost on EngineError (budget tracking)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 38. Cost-budget skip-engine instead of halt-walk
# ---------------------------------------------------------------------------


async def test_walk_skips_costly_engine_but_continues_to_free_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cost-too-high on engine X must not block free engine Y later in the walk.

    Two engines: paid_engine (cost=0.01), free_engine (cost=0.0).
    max_cost_usd=0.0 → paid_engine skipped, free_engine still tried and wins.
    """
    from scrapefold.router import walk

    paid_called: list[bool] = []

    async def _fetch_paid(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        paid_called.append(True)
        return _make_result("cost_paid_engine", url=url)

    paid_engine = type(
        "_StubCostPaid",
        (ScrapeEngine,),
        {
            "NAME": "cost_paid_engine",
            "CAPABILITIES": EngineCapabilities(requires_api_key=True, estimated_cost_usd=0.01),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_paid,
        },
    )
    free_engine = _stub_engine("cost_free_engine", "good")

    monkeypatch.setitem(_REGISTRY, "cost_paid_engine", lambda: paid_engine)
    monkeypatch.setitem(_REGISTRY, "cost_free_engine", lambda: free_engine)

    opts = ScrapeOptions(
        engines=("cost_paid_engine", "cost_free_engine"),
        extra={"max_cost_usd": 0.0, "policy": Policy(paid_allowed=True)},
    )
    result = await walk("https://example.com/", opts)

    # paid engine must never have been invoked.
    assert paid_called == [], "paid engine must not be called when max_cost_usd=0"
    # free engine wins — walk must NOT have halted at the paid engine.
    assert result.engine == "cost_free_engine"
    # Failures must record the budget skip for the paid engine.
    assert any("cost_paid_engine" in f and "budget:cost" in f for f in result.failures), (
        f"expected budget:cost skip for paid engine; got {result.failures}"
    )


# ---------------------------------------------------------------------------
# 39. Empty geography=() means global, passes geography_required gate
# ---------------------------------------------------------------------------


async def test_walk_geography_required_allows_global_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engines with empty geography=() are global and pass geography_required gate.

    Engine has geography=(), policy has geography_required='eu'.
    Engine is NOT skipped; engine is invoked and wins.
    """
    from scrapefold.router import walk

    global_engine_called: list[bool] = []

    async def _fetch_global(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        global_engine_called.append(True)
        return _make_result("geo_global", url=url)

    global_engine = type(
        "_StubGeoGlobal",
        (ScrapeEngine,),
        {
            "NAME": "geo_global",
            # Empty geography = global / no preference → should pass any geography_required
            "CAPABILITIES": EngineCapabilities(requires_api_key=False, geography=()),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_global,
        },
    )

    monkeypatch.setitem(_REGISTRY, "geo_global", lambda: global_engine)

    opts = ScrapeOptions(
        engines=("geo_global",),
        extra={"policy": Policy(geography_required="eu")},
    )
    result = await walk("https://example.com/", opts)

    # Global engine must have been invoked and must win.
    assert global_engine_called == [True], (
        "global engine (geography=()) must be called when geography_required='eu'"
    )
    assert result.engine == "geo_global"
    # No geography skip must appear in failures.
    assert not any("geo_global" in f and "skipped:geography" in f for f in result.failures), (
        f"global engine must not be blocked by geography gate; got {result.failures}"
    )


async def test_walk_helper_increments_budget_on_engine_error(
    stub_ladder: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An engine that raises EngineError still counts toward engines_tried and cost_usd.

    Verifies that the budget is updated even on failure so a subsequent
    max_engines or max_cost_usd check reflects the previous (erroring) attempt.
    """
    from scrapefold.router import walk

    async def _fetch_error(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        raise RuntimeError("simulated network error")

    error_engine = type(
        "_StubCostlyError",
        (ScrapeEngine,),
        {
            "NAME": "costly_error_engine",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False, estimated_cost_usd=0.001),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_error,
        },
    )
    good_engine = _stub_engine("after_error_good", "good")

    monkeypatch.setitem(_REGISTRY, "costly_error_engine", lambda: error_engine)
    monkeypatch.setitem(_REGISTRY, "after_error_good", lambda: good_engine)

    stub_ladder(
        (
            SequentialStep(engine="costly_error_engine"),
            SequentialStep(engine="after_error_good"),
        )
    )

    result = await walk("https://example.com/")

    # Walk must complete (good engine wins after the erroring one).
    assert result.engine == "after_error_good"
    # The erroring engine must appear in failures.
    assert any("costly_error_engine" in f and "error" in f for f in result.failures)


# ---------------------------------------------------------------------------
# 40. Timeout boundary — elapsed_accum_ms == timeout_s*1000 halts the walk
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 41. Budget credits actual cost when engine reports cost_usd > estimate
# ---------------------------------------------------------------------------


async def test_walk_credits_actual_cost_not_just_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If engine reports cost_usd > estimate, budget uses the higher value.

    FakeEngine has estimated_cost_usd=0.001 but returns cost_usd=0.01.
    max_cost_usd=0.005.
    Pre-check: 0.001 < 0.005, so engine is invoked.
    Post-call actual cost = max(0.001, 0.01) = 0.01 > 0.005.
    A second engine attempt must be blocked with budget:cost.
    """
    from scrapefold.router import walk

    second_called: list[bool] = []

    async def _fetch_overcharge(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        # Return empty so the walk tries a second engine, but with a high actual cost.
        return ScrapeResult(
            url=url,
            text="",
            markdown="",
            html=None,
            engine=self.NAME,
            elapsed_ms=1,
            cost_usd=0.01,  # actual cost far exceeds the 0.001 estimate
        )

    async def _fetch_second(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        second_called.append(True)
        return ScrapeResult(
            url=url,
            text=_GOOD_TEXT,
            markdown=f"# {_GOOD_TEXT}",
            html=None,
            engine=self.NAME,
            elapsed_ms=1,
        )

    overcharge_engine = type(
        "_StubOvercharge",
        (ScrapeEngine,),
        {
            "NAME": "ac_overcharge",
            # estimate is low -> passes pre-check; actual will be 10x higher.
            # requires_api_key=False so is_available() returns True without a key.
            "CAPABILITIES": EngineCapabilities(requires_api_key=False, estimated_cost_usd=0.001),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_overcharge,
        },
    )

    second_engine = type(
        "_StubSecond",
        (ScrapeEngine,),
        {
            "NAME": "ac_second",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False, estimated_cost_usd=0.001),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_second,
        },
    )

    monkeypatch.setitem(_REGISTRY, "ac_overcharge", lambda: overcharge_engine)
    monkeypatch.setitem(_REGISTRY, "ac_second", lambda: second_engine)

    # max_cost_usd=0.005 — pre-check on overcharge_engine passes (0.001 < 0.005),
    # but after the call actual cost is 0.01, which exhausts the budget.
    # The second engine (cost=0.001) must be blocked with budget:cost.
    opts = ScrapeOptions(
        engines=("ac_overcharge", "ac_second"),
        extra={"max_cost_usd": 0.005},
    )

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    # Second engine must NOT have been invoked — budget was exhausted by the actual cost.
    assert second_called == [], (
        "second engine must not be called when actual cost from first engine exceeds max_cost_usd"
    )
    failures = exc_info.value.failures
    assert any("ac_second" in f and "budget:cost" in f for f in failures), (
        f"expected budget:cost skip for second engine; got {failures}"
    )


async def test_walk_timeout_boundary_halts_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """elapsed_accum_ms == timeout_s*1000 must halt the walk (>= semantics).

    opts.engines=("boundary_slow", "boundary_next") where boundary_slow returns
    elapsed_ms=1000 and timeout_s=1 (budget = 1000 ms).  After boundary_slow
    completes, elapsed_accum_ms equals exactly timeout_s*1000 — the walk must
    stop and NOT invoke boundary_next.
    """
    from scrapefold.router import walk

    call_log: list[str] = []

    async def _fetch_slow(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        call_log.append(self.NAME)
        # Return empty so the walk would continue — the timeout check is what stops it.
        return ScrapeResult(
            url=url,
            text="",
            markdown="",
            html=None,
            engine=self.NAME,
            elapsed_ms=1000,  # exactly == timeout_s * 1000
        )

    async def _fetch_next(self: ScrapeEngine, url: str, opts: ScrapeOptions) -> ScrapeResult:
        call_log.append(self.NAME)
        return ScrapeResult(
            url=url,
            text=_GOOD_TEXT,
            markdown=f"# {_GOOD_TEXT}",
            html=None,
            engine=self.NAME,
            elapsed_ms=1,
        )

    slow_engine = type(
        "_StubBoundarySlow",
        (ScrapeEngine,),
        {
            "NAME": "boundary_slow",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_slow,
        },
    )
    next_engine = type(
        "_StubBoundaryNext",
        (ScrapeEngine,),
        {
            "NAME": "boundary_next",
            "CAPABILITIES": EngineCapabilities(requires_api_key=False),
            "SUPPORTED_OPTIONS": frozenset(),
            "_fetch": _fetch_next,
        },
    )

    monkeypatch.setitem(_REGISTRY, "boundary_slow", lambda: slow_engine)
    monkeypatch.setitem(_REGISTRY, "boundary_next", lambda: next_engine)

    # timeout_s=1 → budget = 1000 ms; boundary_slow returns exactly 1000 ms elapsed.
    opts = ScrapeOptions(engines=("boundary_slow", "boundary_next"), timeout_s=1)

    with pytest.raises(AllEnginesFailed) as exc_info:
        await walk("https://example.com/", opts)

    # boundary_slow was called; boundary_next must NOT be called (>= semantics).
    assert "boundary_slow" in call_log
    assert "boundary_next" not in call_log, (
        "boundary_next must not be called when elapsed_accum_ms == timeout_s*1000"
    )
    assert any("budget:timeout" in f for f in exc_info.value.failures)
