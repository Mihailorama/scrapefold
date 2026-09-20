"""Pure unit tests for Reciprocal Rank Fusion. No HTTP."""

from __future__ import annotations

import pytest

from scrapefold.search.fusion import _normalize_url, fuse, reciprocal_rank_fusion
from scrapefold.search.types import SearchHit


def _hit(url: str, title: str = "", snippet: str = "", raw: dict | None = None) -> SearchHit:
    return SearchHit(url=url, title=title, snippet=snippet, raw=raw or {})


def test_fuse_is_alias_of_reciprocal_rank_fusion() -> None:
    assert fuse is reciprocal_rank_fusion


def test_scoring_math_matches_rrf_formula() -> None:
    per_engine = {
        "a": [_hit("https://one.com"), _hit("https://two.com")],
        "b": [_hit("https://two.com"), _hit("https://three.com")],
    }

    results = reciprocal_rank_fusion(per_engine, k=60)
    by_url = {r.url: r for r in results}

    # a: pos0 -> 1/61, pos1 -> 1/62 ; b: pos0 -> 1/61, pos1 -> 1/62
    assert by_url["https://one.com"].score == pytest.approx(1 / 61)
    assert by_url["https://two.com"].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_url["https://three.com"].score == pytest.approx(1 / 62)

    # two is returned by both engines -> highest score -> rank 0
    assert results[0].url == "https://two.com"
    assert results[0].rank == 0
    assert [r.rank for r in results] == [0, 1, 2]


def test_score_breakdown_consensus_and_engines_populated() -> None:
    per_engine = {
        "a": [_hit("https://shared.com")],
        "b": [_hit("https://shared.com")],
    }

    (result,) = reciprocal_rank_fusion(per_engine, k=60)

    assert result.consensus == 2
    assert result.engines == ("a", "b")  # sorted for determinism
    assert result.score_breakdown["a"] == pytest.approx(1 / 61)
    assert result.score_breakdown["b"] == pytest.approx(1 / 61)
    assert result.score == pytest.approx(2 / 61)


def test_dedup_normalizes_trailing_slash_casing_and_tracking_params() -> None:
    per_engine = {
        "a": [_hit("https://Example.com/page/?utm_source=x&fbclid=y")],
        "b": [_hit("https://example.com/page")],
    }

    results = reciprocal_rank_fusion(per_engine, k=60)

    assert len(results) == 1
    assert results[0].consensus == 2


def test_normalize_url_helper() -> None:
    assert _normalize_url("https://Example.com/p/") == "https://example.com/p"
    assert _normalize_url("http://Host.COM/a?utm_medium=x&q=1&gclid=z") == "http://host.com/a?q=1"
    assert _normalize_url("https://x.com/") == "https://x.com"
    assert _normalize_url("example.com/y") == "https://example.com/y"


def test_canonical_url_prefers_highest_ranking_engine() -> None:
    # Same page, different original url strings. The engine that ranked it best
    # (lowest position) supplies the canonical url.
    per_engine = {
        "low": [_hit("https://filler.com"), _hit("https://example.com/page?utm_source=low")],
        "high": [_hit("https://example.com/page")],
    }

    results = reciprocal_rank_fusion(per_engine, k=60)
    by_key = {r.url.split("?")[0]: r for r in results}

    # from the pos-0 "high" engine, not the utm-tagged pos-1 "low" variant
    assert by_key["https://example.com/page"].url == "https://example.com/page"
    assert by_key["https://example.com/page"].consensus == 2


def test_longest_title_and_snippet_win() -> None:
    per_engine = {
        "a": [_hit("https://x.com", title="Short", snippet="brief")],
        "b": [
            _hit("https://x.com", title="A much longer title", snippet="a considerably longer bit")
        ],
    }

    (result,) = reciprocal_rank_fusion(per_engine, k=60)

    assert result.title == "A much longer title"
    assert result.snippet == "a considerably longer bit"


def test_tie_break_equal_score_equal_consensus_uses_url() -> None:
    # Symmetric positions -> identical scores, identical consensus (2 each).
    per_engine = {
        "a": [_hit("https://one.com"), _hit("https://two.com")],
        "b": [_hit("https://two.com"), _hit("https://one.com")],
    }

    results = reciprocal_rank_fusion(per_engine, k=60)

    assert results[0].score == pytest.approx(results[1].score)
    assert [r.url for r in results] == ["https://one.com", "https://two.com"]


def test_tie_break_higher_consensus_wins_before_url() -> None:
    # With k=1: solo at pos0 scores 1/2; shared at pos2 in two engines also 1/2.
    # Equal score, but shared has consensus 2 vs solo's 1 -> shared ranks first,
    # even though "a-solo.com" would sort before "z-shared.com".
    per_engine = {
        "a": [_hit("https://x0.com"), _hit("https://x1.com"), _hit("https://z-shared.com")],
        "b": [_hit("https://y0.com"), _hit("https://y1.com"), _hit("https://z-shared.com")],
        "c": [_hit("https://a-solo.com")],
    }

    results = reciprocal_rank_fusion(per_engine, k=1)
    by_url = {r.url: r for r in results}

    assert by_url["https://z-shared.com"].score == pytest.approx(0.5)
    assert by_url["https://a-solo.com"].score == pytest.approx(0.5)
    assert results[0].url == "https://z-shared.com"


def test_fusion_returns_all_distinct_urls_no_count_truncation() -> None:
    per_engine = {"a": [_hit(f"https://site-{i}.com") for i in range(25)]}

    results = reciprocal_rank_fusion(per_engine, k=60)

    assert len(results) == 25
