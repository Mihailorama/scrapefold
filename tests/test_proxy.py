"""Tests for the SessionPool proxy-rotation layer ("proxy over proxy").

Pure unit tests — no engines, no network. Exercise construction/dedup, the
health-aware acquire order, the strike/retire/heal lifecycle, exhaustion, and
the build_pool_from_options / credential-masking helpers.
"""

from __future__ import annotations

from scrapefold.proxy import (
    Session,
    SessionPool,
    _mask,  # type: ignore[attr-defined]
    build_pool_from_options,
)

P1 = "http://p1.example:8000"
P2 = "http://p2.example:8000"
P3 = "http://p3.example:8000"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_dedups_and_preserves_order() -> None:
    pool = SessionPool([P1, P2, P1, P2])
    assert len(pool) == 2
    assert pool.usable_count == 2


def test_include_direct_adds_a_none_session() -> None:
    pool = SessionPool([P1], include_direct=True)
    assert len(pool) == 2
    proxies = {pool.acquire().proxy for _ in range(2)}  # type: ignore[union-attr]
    assert None in proxies and P1 in proxies


def test_empty_proxies_falls_back_to_a_single_direct_session() -> None:
    pool = SessionPool([])
    assert len(pool) == 1
    sess = pool.acquire()
    assert sess is not None and sess.proxy is None


# ---------------------------------------------------------------------------
# Acquire order — healthiest, then least-used
# ---------------------------------------------------------------------------


def test_acquire_spreads_load_across_equally_healthy_sessions() -> None:
    pool = SessionPool([P1, P2])
    # Two acquisitions with no reports should hit both exits (round-robin spread).
    first = pool.acquire()
    second = pool.acquire()
    assert first is not None and second is not None
    assert {first.proxy, second.proxy} == {P1, P2}


def test_acquire_prefers_the_healthier_session() -> None:
    pool = SessionPool([P1, P2])
    s1 = pool.acquire()
    assert s1 is not None
    pool.report(s1, blocked=True)  # s1 now has 1 strike
    # Next acquire should prefer the un-struck exit regardless of use counts.
    s2 = pool.acquire()
    assert s2 is not None and s2.proxy != s1.proxy


# ---------------------------------------------------------------------------
# Strike / retire / heal lifecycle
# ---------------------------------------------------------------------------


def test_report_retires_after_max_errors() -> None:
    pool = SessionPool([P1], max_errors=3)
    sess = pool.acquire()
    assert sess is not None
    pool.report(sess, blocked=True)
    pool.report(sess, blocked=True)
    assert sess.healthy is True  # 2 < 3
    pool.report(sess, blocked=True)
    assert sess.retired is True
    assert pool.usable_count == 0


def test_success_heals_a_strike() -> None:
    pool = SessionPool([P1], max_errors=2)
    sess = pool.acquire()
    assert sess is not None
    pool.report(sess, blocked=True)  # 1 strike
    pool.report(sess, blocked=False)  # heal back to 0
    assert sess.errors == 0
    # Now it takes the full budget again to retire it.
    pool.report(sess, blocked=True)
    assert sess.healthy is True


def test_acquire_returns_none_when_all_retired() -> None:
    pool = SessionPool([P1, P2], max_errors=1)
    for _ in range(2):
        s = pool.acquire()
        assert s is not None
        pool.report(s, blocked=True)  # max_errors=1 → retire immediately
    assert pool.usable() is False
    assert pool.acquire() is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_build_pool_from_options_returns_none_for_empty() -> None:
    assert build_pool_from_options(None) is None
    assert build_pool_from_options(()) is None
    assert build_pool_from_options([]) is None


def test_build_pool_from_options_builds_when_present() -> None:
    pool = build_pool_from_options((P1, P2))
    assert isinstance(pool, SessionPool)
    assert len(pool) == 2


def test_stats_snapshot() -> None:
    pool = SessionPool([P1, P2], max_errors=1)
    s = pool.acquire()
    assert s is not None
    pool.report(s, blocked=True)
    stats = pool.stats()
    assert stats["total"] == 2
    assert stats["usable"] == 1
    assert isinstance(stats["retired"], list) and len(stats["retired"]) == 1


def test_mask_hides_credentials() -> None:
    assert _mask(None) == "direct"
    assert _mask("http://user:secret@host:8000") == "http://***@host:8000"
    assert _mask("http://host:8000") == "http://host:8000"
    # Session label is the masked form so logs never leak the password.
    sess = Session(
        proxy="http://user:secret@host:8000", label=_mask("http://user:secret@host:8000")
    )
    assert "secret" not in sess.label
