"""Structural + semantic tests for per-site-class escalation ladders."""

from __future__ import annotations

from typing import get_args

import pytest

from scrapefold.engines import ENGINE_ALIASES, register_alias, resolve_alias
from scrapefold.ladders import (
    DEFAULT_POLICY,
    GOLDEN_CORPUS,
    LADDERS,
    URL_PATTERNS,
    BudgetExceeded,
    Policy,
    RaceStep,
    SequentialStep,
    SiteClass,
    WalkBudget,
    check_budget,
    classify_url,
    estimate_step_cost,
    flatten_ladder,
    get_default_policy,
    get_ladder,
    is_step_allowed,
    step_engines,
)

# Engine names referenced by ladders. Multi-mode engines are registered as
# distinct canonical names; user-facing aliases live in ENGINE_ALIASES.
_VALID_ENGINES = frozenset(
    {
        # ported (10)
        "requests",
        "firecrawl",
        "scrapingbee",
        "scrapingdog",
        "selenium",
        "jina",
        "cloudflare",
        "crawl4ai",
        "outscraper",
        "apify_linkedin",
        # multi-mode distinct names
        "scrapling_stealth",
        "scrapling_fast",
        "brightdata_unlocker_sync",
        "brightdata_unlocker_async",
        "brightdata_browser",
        # new stealth browsers (open-source)
        "obscura",
        "cloakbrowser",
        # new paid SaaS
        "anysite",
    }
)


# ---------------------------------------------------------------------------
# Structural — every ladder is well-formed
# ---------------------------------------------------------------------------


def test_every_site_class_has_a_ladder() -> None:
    declared = set(get_args(SiteClass))
    mapped = set(LADDERS.keys())
    missing = declared - mapped
    assert not missing, f"SiteClass values without a ladder: {missing}"


def test_every_engine_in_every_ladder_is_a_known_engine() -> None:
    for site_class, ladder in LADDERS.items():
        for step in ladder:
            unknown = set(step_engines(step)) - _VALID_ENGINES
            assert not unknown, f"ladder for {site_class!r} mentions unknown engine(s) {unknown}"


def test_every_ladder_has_at_least_one_step() -> None:
    for site_class, ladder in LADDERS.items():
        assert len(ladder) >= 1, f"empty ladder for {site_class!r}"
        for step in ladder:
            assert step_engines(step), f"step with no engines in {site_class!r}"


def test_get_ladder_returns_known_ladder() -> None:
    flat = flatten_ladder(get_ladder("linkedin_profile"))
    assert "apify_linkedin" in flat


def test_get_ladder_falls_back_to_general_for_unknown_class() -> None:
    flat = flatten_ladder(get_ladder("not_a_real_class"))
    assert flat[0] == "requests"


def test_linkedin_never_starts_with_requests() -> None:
    for cls in (
        "linkedin_profile",
        "linkedin_company",
        "linkedin_post",
        "linkedin_job",
        "linkedin_sales_navigator",
    ):
        first = get_ladder(cls)[0]
        assert "requests" not in step_engines(first), (
            f"{cls} ladder starts with 'requests' — would be blocked"
        )


def test_difficulty_classes_skip_requests() -> None:
    for cls in (
        "cloudflare_protected",
        "datadome_protected",
        "akamai_protected",
        "perimeterx_protected",
    ):
        first = get_ladder(cls)[0]
        engines = step_engines(first)
        assert "requests" not in engines, f"{cls} starts with requests"
        stealth = {"cloakbrowser", "obscura", "scrapling_stealth"}
        assert any(e in stealth for e in engines), f"{cls} first step has no stealth engine"


# ---------------------------------------------------------------------------
# Golden corpus — URL classification regression net
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", GOLDEN_CORPUS)
def test_url_classification_golden_corpus(url: str, expected: str) -> None:
    assert classify_url(url) == expected


def test_every_url_pattern_class_has_corpus_row() -> None:
    """Adding a new URL pattern without a corpus row would defeat the regression net."""
    pattern_classes = {cls for _, cls in URL_PATTERNS}
    corpus_classes = {cls for _, cls in GOLDEN_CORPUS}
    missing = pattern_classes - corpus_classes
    assert not missing, f"URL patterns without corpus coverage: {missing}"


def test_every_classifiable_class_has_corpus_row() -> None:
    """Every SiteClass that COULD be reached by classify_url must have ≥1 corpus row,
    plus 'static_general' which is the fallback."""
    corpus_classes = {cls for _, cls in GOLDEN_CORPUS}
    pattern_classes = {cls for _, cls in URL_PATTERNS}
    reachable = pattern_classes | {"static_general"}
    missing = reachable - corpus_classes
    assert not missing, f"reachable classes lacking corpus rows: {missing}"


# ---------------------------------------------------------------------------
# Policy enforcement
# ---------------------------------------------------------------------------


def test_is_step_allowed_blocks_paid_when_policy_forbids() -> None:
    paid_step = SequentialStep(engine="firecrawl", estimated_cost_usd=0.005)
    policy = Policy(paid_allowed=False)
    allowed, reason = is_step_allowed(paid_step, policy)
    assert not allowed
    assert reason == "paid_not_allowed"


def test_is_step_allowed_permits_free_step_even_when_paid_blocked() -> None:
    free_step = SequentialStep(engine="requests", estimated_cost_usd=0.0)
    allowed, reason = is_step_allowed(free_step, Policy(paid_allowed=False))
    assert allowed
    assert reason is None


def test_is_step_allowed_legal_constraint_matches() -> None:
    step = SequentialStep(
        engine="apify_linkedin",
        legal_constraints=frozenset({"consent_required_linkedin"}),
    )
    policy = Policy(legal_constraints_blocked=frozenset({"consent_required_linkedin"}))
    allowed, reason = is_step_allowed(step, policy)
    assert not allowed
    assert reason is not None
    assert "consent_required_linkedin" in reason


def test_is_step_allowed_legal_constraint_no_match() -> None:
    step = SequentialStep(
        engine="firecrawl",
        legal_constraints=frozenset({"some_other_tag"}),
    )
    policy = Policy(legal_constraints_blocked=frozenset({"different_tag"}))
    allowed, reason = is_step_allowed(step, policy)
    assert allowed
    assert reason is None


def test_is_step_allowed_geography_match() -> None:
    step = SequentialStep(engine="scrapingdog", geography=("ru",))
    policy = Policy(geography_required="us")
    allowed, reason = is_step_allowed(step, policy)
    assert not allowed
    assert reason is not None
    assert "geography" in reason


def test_is_step_allowed_geography_satisfied() -> None:
    step = SequentialStep(engine="scrapingdog", geography=("ru", "us"))
    policy = Policy(geography_required="us")
    allowed, _ = is_step_allowed(step, policy)
    assert allowed


def test_government_default_policy_blocks_paid() -> None:
    """government class ships with paid_allowed=False by default."""
    assert "government" in DEFAULT_POLICY
    assert DEFAULT_POLICY["government"].paid_allowed is False


def test_get_default_policy_returns_permissive_for_unknown_class() -> None:
    policy = get_default_policy("static_general")
    assert policy.paid_allowed is True


# ---------------------------------------------------------------------------
# Budget contract
# ---------------------------------------------------------------------------


def test_check_budget_passes_when_under_all_ceilings() -> None:
    step = SequentialStep(engine="requests")
    walk = WalkBudget()
    check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.05)


def test_check_budget_aborts_on_cost_overshoot() -> None:
    step = SequentialStep(engine="firecrawl", estimated_cost_usd=0.10)
    walk = WalkBudget()
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.05)
    assert exc.value.reason == "cost"


def test_check_budget_aborts_on_per_step_cost_cap() -> None:
    step = SequentialStep(
        engine="brightdata_unlocker_sync",
        estimated_cost_usd=0.02,
        max_extra_cost_usd=0.01,
    )
    walk = WalkBudget()
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.50)
    assert exc.value.reason == "cost"


def test_check_budget_aborts_on_elapsed_overshoot() -> None:
    step = SequentialStep(engine="requests")
    walk = WalkBudget(elapsed_ms=61_000)
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.05)
    assert exc.value.reason == "elapsed"


def test_check_budget_aborts_on_reclassification_cap() -> None:
    step = SequentialStep(engine="requests")
    walk = WalkBudget(reclassifications=WalkBudget.MAX_RECLASSIFICATIONS)
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.05)
    assert exc.value.reason == "reclassifications"


def test_check_budget_race_fanout_counts_all_engines() -> None:
    """A 3-engine RaceStep must not start with only 1 engine slot remaining."""
    step = RaceStep(engines=("firecrawl", "scrapingbee", "scrapingdog"))
    walk = WalkBudget(engines_tried={"requests", "crawl4ai", "scrapling_stealth"})
    with pytest.raises(BudgetExceeded) as exc:
        check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.50)
    assert exc.value.reason == "engines_count"


def test_check_budget_race_fanout_passes_when_room() -> None:
    step = RaceStep(engines=("firecrawl", "scrapingbee"))
    walk = WalkBudget(engines_tried={"requests"})
    check_budget(step, walk, timeout_s=60, max_engines=4, max_cost_usd=0.50)


# ---------------------------------------------------------------------------
# Sum-type semantics — RaceStep / SequentialStep
# ---------------------------------------------------------------------------


def test_race_step_default_winner_policy_is_first_non_suspicious() -> None:
    step = RaceStep(engines=("firecrawl", "scrapingbee"))
    assert step.winner_policy == "first_non_suspicious"


def test_race_step_default_cancel_policy_is_cancel_immediately() -> None:
    step = RaceStep(engines=("a", "b"))
    assert step.cancel_policy == "cancel_immediately"


def test_race_step_default_budget_accounting_is_winner_only() -> None:
    """Default biased to winner_only; paid steps in LADDERS override to sum_all."""
    step = RaceStep(engines=("a", "b"))
    assert step.budget_accounting == "winner_only"


def test_paid_linkedin_race_steps_use_sum_all_billing() -> None:
    """LinkedIn race steps fan out 2-3 paid vendors — each call is billed."""
    for cls in ("linkedin_profile", "linkedin_company", "linkedin_job"):
        first = get_ladder(cls)[0]
        assert isinstance(first, RaceStep), f"{cls} first step is not a race"
        assert first.budget_accounting == "sum_all", (
            f"{cls} race must bill all engines, not winner_only"
        )


def test_step_engines_returns_correct_names() -> None:
    seq = SequentialStep(engine="firecrawl")
    race = RaceStep(engines=("a", "b", "c"))
    assert step_engines(seq) == ("firecrawl",)
    assert step_engines(race) == ("a", "b", "c")


# ---------------------------------------------------------------------------
# Multi-mode engine separation
# ---------------------------------------------------------------------------


def test_multi_mode_engines_appear_as_distinct_ladder_entries() -> None:
    """Ladders must use canonical distinct names so WalkBudget dedup is unambiguous."""
    all_engines: set[str] = set()
    for ladder in LADDERS.values():
        for step in ladder:
            all_engines.update(step_engines(step))
    assert "scrapling" not in all_engines, (
        "ladders must use 'scrapling_stealth'/'scrapling_fast', not bare 'scrapling'"
    )
    assert "brightdata_unlocker" not in all_engines, (
        "ladders must use 'brightdata_unlocker_sync'/'_async'"
    )


def test_engine_aliases_dict_exists_and_is_mutable() -> None:
    assert isinstance(ENGINE_ALIASES, dict)


def test_resolve_alias_round_trip() -> None:
    register_alias("scrapling", "scrapling_stealth")
    try:
        assert resolve_alias("scrapling") == "scrapling_stealth"
        assert resolve_alias("not_an_engine") == "not_an_engine"
    finally:
        ENGINE_ALIASES.pop("scrapling", None)


# ---------------------------------------------------------------------------
# Visited-class loop guard
# ---------------------------------------------------------------------------


def test_walk_budget_visited_site_classes_starts_empty() -> None:
    walk = WalkBudget()
    assert walk.visited_site_classes == set()


def test_walk_budget_visited_site_classes_can_track_reclassification_chain() -> None:
    walk = WalkBudget()
    walk.visited_site_classes.add("static_general")
    walk.visited_site_classes.add("cloudflare_protected")
    assert len(walk.visited_site_classes) == 2
    walk.visited_site_classes.add("static_general")
    assert len(walk.visited_site_classes) == 2


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def test_estimate_step_cost_call_unit_returns_base() -> None:
    step = SequentialStep(
        engine="firecrawl",
        estimated_cost_usd=0.005,
        billing_unit="call",
    )
    assert estimate_step_cost(step) == pytest.approx(0.005)


def test_estimate_step_cost_gb_unit_scales_with_response_size() -> None:
    step = SequentialStep(
        engine="brightdata_browser",
        estimated_cost_usd=1.0,
        billing_unit="gb",
    )
    actual = estimate_step_cost(step, avg_response_mb=10.0)
    assert actual == pytest.approx(10.0 / 1024, rel=1e-3)


def test_estimate_step_cost_gb_default_response_mb() -> None:
    step = SequentialStep(
        engine="brightdata_browser",
        estimated_cost_usd=1.0,
        billing_unit="gb",
    )
    assert estimate_step_cost(step) == pytest.approx(2.0 / 1024, rel=1e-3)
