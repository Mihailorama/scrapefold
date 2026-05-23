"""Walk a per-site-class ladder and return the first good result.

Pure orchestration — engines come from ``scrapefold.engines.get_engine``, the
ladder from ``scrapefold.ladders.get_ladder``. The router does no I/O itself;
``Policy`` is enforced via ``ladders.is_step_allowed`` and ``WalkBudget`` via
``ladders.check_budget``.

v0.1 note: ``RaceStep`` members are walked sequentially (one at a time).
Concurrent fan-out with first-good-wins cancellation is deferred to v0.2.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from scrapefold.detection import is_suspicious
from scrapefold.engines import get_engine
from scrapefold.engines.base import EngineError
from scrapefold.ladders import (
    AllEnginesFailed,
    BudgetExceeded,
    Policy,
    RaceStep,
    SequentialStep,
    SiteClass,
    WalkBudget,
    check_budget,
    classify_url,
    estimate_step_cost,
    get_default_policy,
    get_ladder,
    is_step_allowed,
)
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENGINES = 4
_DEFAULT_MAX_COST_USD = 0.05
_DEFAULT_AVG_RESPONSE_MB = 2.0


def _resolve_policy(opts: ScrapeOptions, site_class: SiteClass) -> Policy:
    override = opts.extra.get("policy")
    if isinstance(override, Policy):
        return override
    return get_default_policy(site_class)


async def _try_engine(
    engine_name: str,
    url: str,
    opts: ScrapeOptions,
    failures: list[str],
) -> ScrapeResult | None:
    """Attempt a single named engine and return its result, or None on skip/failure.

    Side-effects: appends to *failures* when the engine is skipped or errors.
    """
    try:
        engine_cls = get_engine(engine_name)
    except KeyError:
        logger.debug("router: unknown engine=%s", engine_name)
        failures.append(f"{engine_name}:unknown")
        return None

    canonical = engine_cls.NAME
    engine = engine_cls()
    if not engine.is_available():
        logger.debug("router: skip engine=%s not available", canonical)
        failures.append(f"{canonical}:unavailable")
        return None

    try:
        result = await engine.scrape(url, opts)
    except EngineError as exc:
        failures.append(f"{canonical}:error:{exc.message}")
        return None

    if result.is_empty():
        failures.append(f"{canonical}:empty")
        return None
    if is_suspicious(result):
        failures.append(f"{canonical}:suspicious")
        return None

    return result


async def walk(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
    """Walk the resolved ladder and return the first non-empty result.

    If ``opts.engines`` is non-empty, those engine names are tried in order
    instead of the default per-site-class ladder.  Duplicate names (by
    canonical ``engine_cls.NAME``) are skipped after the first attempt.

    Raises ``AllEnginesFailed`` if every step fails or is skipped.

    v0.1 ladder walk: both ``SequentialStep`` and ``RaceStep`` entries are
    walked sequentially (one engine at a time).  Concurrent fan-out with
    first-good-wins cancellation is deferred to v0.2.
    """
    opts = opts or ScrapeOptions()
    failures: list[str] = []

    # --- opts.engines override path ---
    # Empty tuple is coerced to None (use default ladder) for safety.
    if opts.engines:
        # Resolve policy and budget settings so they apply to explicit engines too.
        site_class_override = classify_url(url)
        policy_override = _resolve_policy(opts, site_class_override)
        max_cost_usd_override = float(opts.extra.get("max_cost_usd", _DEFAULT_MAX_COST_USD))
        max_engines_override = int(opts.extra.get("max_engines", _DEFAULT_MAX_ENGINES))
        cost_accum: float = 0.0
        engines_called: int = 0
        elapsed_accum_ms: float = 0.0
        seen_canonical: set[str] = set()  # dedup by canonical NAME

        for name in opts.engines:
            # Budget gate: max_engines ceiling (checked before any engine work).
            if engines_called >= max_engines_override:
                logger.info("router: walk halted budget=max_engines (override path)")
                failures.append("budget:max_engines")
                break

            # Budget gate: elapsed-time ceiling (checked before any engine work).
            if elapsed_accum_ms > opts.timeout_s * 1000:
                logger.info("router: walk halted budget=timeout (override path)")
                failures.append("budget:timeout")
                break

            try:
                engine_cls = get_engine(name)
            except KeyError:
                logger.debug("router: unknown engine=%s", name)
                failures.append(f"{name}:unknown")
                continue

            canonical = engine_cls.NAME

            # Dedup: skip if this canonical name was already attempted.
            if canonical in seen_canonical:
                logger.debug("router: skip engine=%s reason=duplicate", canonical)
                failures.append(f"{canonical}:duplicate")
                continue
            seen_canonical.add(canonical)

            # Policy gate: block paid engines when policy forbids them.
            # Synthesising a SequentialStep would carry cost=0.0 (unknown),
            # so we check engine capabilities directly instead.
            if not policy_override.paid_allowed and engine_cls().CAPABILITIES.requires_api_key:
                logger.debug("router: skip engine=%s reason=paid_not_allowed", canonical)
                failures.append(f"{canonical}:skipped:paid_not_allowed")
                continue

            # Budget gate: pre-check whether this engine's cost would exceed
            # the remaining budget.  We read the cost from CAPABILITIES so
            # max_cost_usd=0 correctly blocks the very first paid engine.
            next_cost = float(engine_cls.CAPABILITIES.estimated_cost_usd or 0.0)
            if cost_accum + next_cost > max_cost_usd_override:
                logger.info("router: walk halted budget=cost (override path)")
                failures.append(f"{canonical}:skipped:budget:cost")
                break

            engine = engine_cls()
            if not engine.is_available():
                logger.debug("router: skip engine=%s not available", canonical)
                failures.append(f"{canonical}:unavailable")
                continue

            try:
                result = await engine.scrape(url, opts)
            except EngineError as exc:
                cost_accum += next_cost
                engines_called += 1
                elapsed_accum_ms += exc.elapsed_ms
                failures.append(f"{canonical}:error:{exc.message}")
                continue

            cost_accum += next_cost
            engines_called += 1
            elapsed_accum_ms += result.elapsed_ms

            if result.is_empty():
                failures.append(f"{canonical}:empty")
                continue
            if is_suspicious(result):
                failures.append(f"{canonical}:suspicious")
                continue

            return replace(result, failures=failures)

        raise AllEnginesFailed(url=url, failures=failures)

    # --- Default ladder path ---
    site_class = classify_url(url)
    policy = _resolve_policy(opts, site_class)
    ladder = get_ladder(site_class)
    budget = WalkBudget(visited_site_classes={site_class})

    max_engines = int(opts.extra.get("max_engines", _DEFAULT_MAX_ENGINES))
    max_cost_usd = float(opts.extra.get("max_cost_usd", _DEFAULT_MAX_COST_USD))
    avg_response_mb = float(opts.extra.get("avg_response_mb", _DEFAULT_AVG_RESPONSE_MB))

    for step in ladder:
        # Determine the list of engine names to attempt for this step.
        # SequentialStep → single engine.
        # RaceStep → multiple engines tried sequentially in v0.1 (concurrent
        #            fan-out with first-good-wins is deferred to v0.2).
        # Unknown step type → skip with a debug log.
        if isinstance(step, SequentialStep):
            engines_to_try = [step.engine]
        elif isinstance(step, RaceStep):
            engines_to_try = list(step.engines)
            logger.debug(
                "router: RaceStep walked sequentially in v0.1 (fan-out deferred): %r",
                step,
            )
        else:
            logger.debug("router: skip unknown step type: %r", step)
            continue

        # Policy + budget check operate on the step as a whole before we enter
        # the per-engine inner loop.
        allowed, reason = is_step_allowed(step, policy)
        if not allowed:
            # Record a skip entry for each engine in the step.
            for eng_name in engines_to_try:
                failures.append(f"{eng_name}:skipped:{reason}")
            logger.debug("router: skip step reason=%s: %r", reason, step)
            continue

        try:
            check_budget(
                step,
                budget,
                timeout_s=opts.timeout_s,
                max_engines=max_engines,
                max_cost_usd=max_cost_usd,
                avg_response_mb=avg_response_mb,
            )
        except BudgetExceeded as exc:
            logger.info("router: walk halted budget=%s", exc.reason)
            failures.append(f"budget:{exc.reason}")
            break

        # Per-engine cost share: divide the step cost evenly across the engines
        # in the step (for SequentialStep this is just the full step cost).
        step_cost = estimate_step_cost(step, avg_response_mb=avg_response_mb)
        per_engine_cost = step_cost / max(len(engines_to_try), 1)

        for engine_name in engines_to_try:
            try:
                engine_cls = get_engine(engine_name)
            except KeyError:
                logger.debug("router: unknown engine=%s", engine_name)
                failures.append(f"{engine_name}:unknown")
                continue

            canonical = engine_cls.NAME
            if canonical in budget.engines_tried:
                logger.debug("router: skip already-tried engine=%s", canonical)
                continue

            engine = engine_cls()
            if not engine.is_available():
                logger.debug("router: skip engine=%s not available", canonical)
                failures.append(f"{canonical}:unavailable")
                budget.engines_tried.add(canonical)
                continue

            budget.engines_tried.add(canonical)

            try:
                result = await engine.scrape(url, opts)
            except EngineError as exc:
                budget.elapsed_ms += exc.elapsed_ms
                budget.cost_usd += per_engine_cost
                failures.append(f"{canonical}:error:{exc.message}")
                continue

            budget.elapsed_ms += result.elapsed_ms
            budget.cost_usd += per_engine_cost

            if result.is_empty():
                failures.append(f"{canonical}:empty")
                continue
            if is_suspicious(result):
                failures.append(f"{canonical}:suspicious")
                continue

            # First good result in this step wins; remaining race members are skipped.
            return replace(result, failures=failures)

    raise AllEnginesFailed(url=url, failures=failures)


__all__ = ["walk"]
