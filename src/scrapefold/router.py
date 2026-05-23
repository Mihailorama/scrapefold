"""Walk a per-site-class ladder and return the first good result.

Pure orchestration — engines come from ``scrapefold.engines.get_engine``, the
ladder from ``scrapefold.ladders.get_ladder``. The router does no I/O itself;
``Policy`` is enforced via ``ladders.is_step_allowed`` and ``WalkBudget`` via
``ladders.check_budget``.
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
    instead of the default per-site-class ladder.

    Raises ``AllEnginesFailed`` if every step fails or is skipped.
    ``RaceStep`` entries are currently skipped — concurrent fan-out lands in
    a follow-up slice.
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
        cost_accum: float = 0.0

        for name in opts.engines:
            try:
                engine_cls = get_engine(name)
            except KeyError:
                logger.debug("router: unknown engine=%s", name)
                failures.append(f"{name}:unknown")
                continue

            canonical = engine_cls.NAME

            # Policy gate: block paid engines when policy forbids them.
            # Synthesising a SequentialStep would carry cost=0.0 (unknown),
            # so we check engine capabilities directly instead.
            if not policy_override.paid_allowed and engine_cls().CAPABILITIES.requires_api_key:
                logger.debug("router: skip engine=%s reason=paid_not_allowed", canonical)
                failures.append(f"{canonical}:skipped:paid_not_allowed")
                continue

            # Budget gate: respect max_cost_usd even on the override path.
            # We don't know the step cost without a ladder entry, so we
            # skip cost accumulation but still block if budget is already
            # exhausted (cost_accum > max_cost_usd_override).
            if cost_accum > max_cost_usd_override:
                logger.info("router: walk halted budget=cost (override path)")
                failures.append("budget:cost")
                break

            engine = engine_cls()
            if not engine.is_available():
                logger.debug("router: skip engine=%s not available", canonical)
                failures.append(f"{canonical}:unavailable")
                continue

            try:
                result = await engine.scrape(url, opts)
            except EngineError as exc:
                failures.append(f"{canonical}:error:{exc.message}")
                continue

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
        if not isinstance(step, SequentialStep):
            logger.debug("router: skip non-sequential step (race fan-out TBD): %r", step)
            continue

        allowed, reason = is_step_allowed(step, policy)
        if not allowed:
            logger.debug("router: skip engine=%s reason=%s", step.engine, reason)
            failures.append(f"{step.engine}:skipped:{reason}")
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

        try:
            engine_cls = get_engine(step.engine)
        except KeyError:
            logger.debug("router: unknown engine=%s", step.engine)
            failures.append(f"{step.engine}:unknown")
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
        step_cost = estimate_step_cost(step, avg_response_mb=avg_response_mb)

        try:
            result = await engine.scrape(url, opts)
        except EngineError as exc:
            budget.elapsed_ms += exc.elapsed_ms
            budget.cost_usd += step_cost
            failures.append(f"{canonical}:error:{exc.message}")
            continue

        budget.elapsed_ms += result.elapsed_ms
        budget.cost_usd += step_cost

        if result.is_empty():
            failures.append(f"{canonical}:empty")
            continue
        if is_suspicious(result):
            failures.append(f"{canonical}:suspicious")
            continue
        return replace(result, failures=failures)

    raise AllEnginesFailed(url=url, failures=failures)


__all__ = ["walk"]
