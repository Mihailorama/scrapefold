"""Walk a per-site-class ladder and return the first good result.

Pure orchestration — engines come from ``scrapefold.engines.get_engine``, the
ladder from ``scrapefold.ladders.get_ladder``. The router does no I/O itself;
policy and cost are enforced uniformly via ``_attempt_engine`` for every
engine regardless of whether it arrives via the default ladder or
``opts.engines`` override.

Post-0.2 note: ``RaceStep`` members are still walked sequentially (one at a
time). Concurrent fan-out with first-good-wins cancellation is deferred to a
future router cycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import tldextract

from scrapefold.detection import is_suspicious
from scrapefold.engines import get_engine
from scrapefold.engines.base import EngineError, RedirectScopeViolation, ScrapeEngine
from scrapefold.ladders import (
    AllEnginesFailed,
    Policy,
    RaceStep,
    SequentialStep,
    SiteClass,
    WalkBudget,
    classify_url,
    estimate_step_cost,
    get_default_policy,
    get_ladder,
)
from scrapefold.options import ScrapeOptions
from scrapefold.pool import EnginePool
from scrapefold.proxy import SessionPool, build_pool_from_options
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ENGINES = 4
_DEFAULT_MAX_COST_USD = 0.05
_DEFAULT_AVG_RESPONSE_MB = 2.0
_DEFAULT_PROXY_ROTATIONS = 2
"""Extra same-engine retries behind a fresh exit IP before escalating a tier."""


def _resolve_session_pool(opts: ScrapeOptions) -> SessionPool | None:
    """Return the proxy rotation pool for this walk, or ``None`` when unset.

    A pre-built ``extra["proxy_pool"]`` (caller-owned, may span a whole crawl)
    wins over ``opts.proxies`` (a static list turned into a per-walk pool).
    """
    prebuilt = opts.extra.get("proxy_pool")
    if isinstance(prebuilt, SessionPool):
        return prebuilt
    return build_pool_from_options(opts.proxies)


# ---------------------------------------------------------------------------
# Probe cache (P1 #7)
# ---------------------------------------------------------------------------

_PROBE_CACHE: dict[tuple[str, str], bool] = {}


def _probe_scope_key(engine_cls: type[ScrapeEngine], url: str) -> tuple[str, str] | None:
    """Return the ``(engine_name, scope_key)`` cache key, or ``None`` if scope=='none'."""
    scope = getattr(engine_cls, "PROBE_SCOPE", "none")
    if scope == "none":
        return None
    if scope == "per_url":
        return (engine_cls.NAME, url)
    if scope == "per_domain":
        ext = tldextract.extract(url)
        domain = f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain
        return (engine_cls.NAME, domain)
    if scope == "per_session":
        return (engine_cls.NAME, "_session")
    return None


def _resolve_policy(opts: ScrapeOptions, site_class: SiteClass) -> Policy:
    override = opts.extra.get("policy")
    if isinstance(override, Policy):
        return override
    return get_default_policy(site_class)


# ---------------------------------------------------------------------------
# Return value from _attempt_engine
# ---------------------------------------------------------------------------


@dataclass
class _AttemptResult:
    """Return value from ``_attempt_engine``.

    Decouples the three possible outcomes cleanly:
    - ``result`` is set → winner found (keep_walking is False).
    - ``result`` is None, ``keep_walking`` is True  → skip/fail, try next.
    - ``result`` is None, ``keep_walking`` is False → budget exhausted, stop.

    ``cost_delta`` and ``elapsed_delta`` are always >= 0 and must be applied
    by the caller to its running budget counters.
    """

    result: ScrapeResult | None
    keep_walking: bool
    cost_delta: float = 0.0
    elapsed_delta: float = 0.0


# ---------------------------------------------------------------------------
# Unified per-engine gate helper
# ---------------------------------------------------------------------------


async def _attempt_engine(
    engine_name: str,
    url: str,
    opts: ScrapeOptions,
    policy: Policy,
    budget_cost_usd: float,
    budget_engines_tried: set[str],
    dedup_seen: set[str],
    max_engines: int,
    max_cost_usd: float,
    failures: list[str],
    proxy_pool: SessionPool | None = None,
) -> _AttemptResult:
    """Try one engine through all gates.

    Returns an ``_AttemptResult`` describing what happened.  The caller must
    add ``result.cost_delta`` and ``result.elapsed_delta`` to its running
    budget accumulators.

    Two sets are maintained by the caller and mutated here:
    - ``dedup_seen``: every canonical name ever encountered (prevents retrying
      unavailable/skipped engines when the same name appears twice in the list).
    - ``budget_engines_tried``: only engines that were actually invoked (passed
      gates 1-6 and had ``engine.scrape()`` called). The ``max_engines`` ceiling
      is checked against this set so unavailable engines don't consume slots.

    Gates checked in order (first failure wins):
    1. Registry lookup — unknown name → skip (keep walking).
    2. Dedup by canonical NAME — already seen → skip (keep walking).
    3. Policy gate — paid_not_allowed → skip (keep walking).
    3b. Policy gate — legal_constraints_blocked → skip (keep walking).
    3c. Policy gate — geography_required → skip (keep walking).
    4. Max-engines budget — ceiling reached → stop walk.
    5. Cost budget — would exceed max_cost_usd → stop walk.
    6. Availability — engine.is_available() → skip (keep walking).
    6b. Probe cache — prior probe returned False → skip (keep walking).
    7. Invoke engine.scrape().
    8. Quality checks — empty / suspicious → skip (keep walking).
    9. Winner — return result (stop walk).
    """
    # 1. Registry lookup
    try:
        engine_cls = get_engine(engine_name)
    except KeyError:
        logger.debug("router: unknown engine=%s", engine_name)
        failures.append(f"{engine_name}:unknown")
        return _AttemptResult(result=None, keep_walking=True)

    canonical = engine_cls.NAME

    # 2. Dedup — use dedup_seen (covers unavailable + invoked engines alike)
    if canonical in dedup_seen:
        logger.debug("router: skip already-tried engine=%s", canonical)
        return _AttemptResult(result=None, keep_walking=True)

    # 3. Policy gate — check per-engine capabilities, not step metadata
    if not policy.paid_allowed and engine_cls.CAPABILITIES.requires_api_key:
        logger.debug("router: skip engine=%s reason=paid_not_allowed", canonical)
        failures.append(f"{canonical}:skipped:paid_not_allowed")
        return _AttemptResult(result=None, keep_walking=True)

    # 3b. Policy gate — legal_constraints
    if policy.legal_constraints_blocked:
        blocked = set(engine_cls.CAPABILITIES.legal_constraints) & set(
            policy.legal_constraints_blocked
        )
        if blocked:
            logger.debug("router: skip engine=%s reason=legal:%s", canonical, sorted(blocked))
            failures.append(f"{canonical}:skipped:legal:{sorted(blocked)}")
            return _AttemptResult(result=None, keep_walking=True)

    # 3c. Policy gate — geography_required.
    # Empty geography=() means "global / no preference" — those engines pass.
    # Only block engines that declare a non-empty geography that excludes the
    # required region.
    if (
        policy.geography_required
        and engine_cls.CAPABILITIES.geography  # non-empty → engine has a declared region
        and policy.geography_required not in engine_cls.CAPABILITIES.geography
    ):
        logger.debug(
            "router: skip engine=%s reason=geography:%s", canonical, policy.geography_required
        )
        failures.append(f"{canonical}:skipped:geography:{policy.geography_required}")
        return _AttemptResult(result=None, keep_walking=True)

    # 4. Max-engines budget
    if len(budget_engines_tried) >= max_engines:
        logger.info("router: walk halted budget=max_engines")
        failures.append("budget:max_engines")
        return _AttemptResult(result=None, keep_walking=False)

    # 5. Cost budget — use engine CAPABILITIES fields to build the cost estimate
    # (P1 #3: use the engine's own avg_response_mb_estimate for gb-billed engines).
    # Always call estimate_step_cost via a synthetic SequentialStep so the routing
    # logic goes through one code path.  The cost / billing_unit come from the
    # engine's CAPABILITIES (not the ladder step), which is the authority for
    # per-engine costs.
    # Cost-too-high is per-engine, not walk-wide: a cheaper engine downstream
    # may still be tried, so we skip this engine but keep walking.
    engine_mb = engine_cls.CAPABILITIES.avg_response_mb_estimate
    _cost_step = SequentialStep(
        engine=canonical,
        estimated_cost_usd=float(engine_cls.CAPABILITIES.estimated_cost_usd or 0.0),
        billing_unit=engine_cls.CAPABILITIES.billing_unit,
    )
    engine_cost = estimate_step_cost(_cost_step, avg_response_mb=engine_mb)
    if budget_cost_usd + engine_cost > max_cost_usd:
        logger.debug("router: skip engine=%s reason=budget:cost", canonical)
        failures.append(f"{canonical}:skipped:budget:cost")
        return _AttemptResult(result=None, keep_walking=True)

    # 6. Availability — checked BEFORE crediting the budget counter.
    # Unavailable engines are added to dedup_seen (so duplicates in the list
    # are still collapsed), but NOT to budget_engines_tried — max_engines counts
    # only engines that are actually invoked, not ones that never ran.
    engine = engine_cls()
    if not engine.is_available():
        logger.debug("router: skip engine=%s not available", canonical)
        failures.append(f"{canonical}:unavailable")
        dedup_seen.add(canonical)
        return _AttemptResult(result=None, keep_walking=True)

    # 6b. Probe cache (P1 #7) — skip engine if a prior probe returned False;
    # run the probe (once per scope) if no cached result exists yet.
    # A cached False means the engine is known-bad for this URL/domain/session;
    # a cached True (or no probe defined) means proceed.
    cache_key = _probe_scope_key(engine_cls, url)
    if cache_key is not None:
        cached_probe = _PROBE_CACHE.get(cache_key)
        if cached_probe is False:
            logger.debug("router: probe cache says skip engine=%s for %s", canonical, url)
            failures.append(f"{canonical}:probe_cache_skip")
            dedup_seen.add(canonical)
            return _AttemptResult(result=None, keep_walking=True)
        if cached_probe is None and hasattr(engine, "probe"):
            probe_ok = await engine.probe(url)
            _PROBE_CACHE[cache_key] = probe_ok
            if not probe_ok:
                logger.debug("router: probe failed engine=%s for %s", canonical, url)
                failures.append(f"{canonical}:probe_failed")
                dedup_seen.add(canonical)
                return _AttemptResult(result=None, keep_walking=True)

    # Mark as seen and as tried before the call so errors still count.
    dedup_seen.add(canonical)
    budget_engines_tried.add(canonical)

    # 7-9. Invoke (optionally rotating exit IPs) + quality gate.
    #
    # When a proxy rotation pool is active AND the engine reads the unified
    # ``proxy`` option, a blocked / failed response retries the SAME engine
    # behind a fresh exit IP (up to ``proxy_max_rotations`` times) before the
    # walk escalates to the next, more expensive tier — the "proxy over proxy"
    # layer. When no pool is active the loop runs exactly once with the caller's
    # opts, reproducing the original single-shot behavior.
    #
    # Post-call accounting uses the higher of estimated vs reported cost so that
    # engines that overspend (ScrapingBee extra credits, Cloudflare 2-request
    # fallback) actually deplete the budget; costs accumulate across rotations.
    use_rotation = proxy_pool is not None and "proxy" in engine_cls.SUPPORTED_OPTIONS
    max_rotations = (
        int(opts.extra.get("proxy_max_rotations", _DEFAULT_PROXY_ROTATIONS)) if use_rotation else 0
    )

    total_cost = 0.0
    total_elapsed = 0.0
    rotation = 0
    while True:
        session = None
        call_opts = opts
        if use_rotation and proxy_pool is not None:
            session = proxy_pool.acquire()
            if session is None:
                # Pool exhausted. Never silently fall back to the real IP against
                # the caller's clear intent to proxy — skip this engine instead.
                if rotation == 0:
                    budget_engines_tried.discard(canonical)
                    failures.append(f"{canonical}:proxy_pool_exhausted")
                break
            call_opts = opts.with_updates(proxy=session.proxy)

        # 7. Invoke
        try:
            result = await engine.scrape(url, call_opts)
        except RedirectScopeViolation as exc:
            # SSRF guard: a different exit IP would follow the same off-host
            # redirect, so terminate the walk regardless of rotation.
            target = exc.target or exc.message
            logger.warning(
                "router: redirect_offhost url=%s target=%s — walk terminated", url, target
            )
            failures.append(f"{canonical}:redirect_offhost:{target}")
            return _AttemptResult(
                result=None,
                keep_walking=False,
                cost_delta=total_cost,
                elapsed_delta=total_elapsed + float(exc.elapsed_ms),
            )
        except EngineError as exc:
            # If the underlying cause is an ImportError the SDK is not installed.
            # Treat that as "unavailable" — no paid call was made so no cost is
            # credited and the slot is NOT consumed.
            if isinstance(exc.__cause__, ImportError):
                import_exc: ImportError = exc.__cause__
                budget_engines_tried.discard(canonical)
                mod = import_exc.name or str(import_exc)
                logger.debug("router: skip engine=%s reason=missing_sdk:%s", canonical, mod)
                failures.append(f"{canonical}:unavailable:missing_sdk:{mod}")
                return _AttemptResult(
                    result=None,
                    keep_walking=True,
                    cost_delta=total_cost,
                    elapsed_delta=total_elapsed,
                )
            # Credit cost even on error — the paid request was made.
            total_cost += engine_cost
            total_elapsed += float(exc.elapsed_ms)
            if session is not None and proxy_pool is not None:
                proxy_pool.report(session, blocked=True)
            if (
                use_rotation
                and rotation < max_rotations
                and proxy_pool is not None
                and proxy_pool.usable()
            ):
                rotation += 1
                logger.debug("router: rotate exit IP after error engine=%s", canonical)
                continue
            failures.append(f"{canonical}:error:{exc.message}")
            return _AttemptResult(
                result=None, keep_walking=True, cost_delta=total_cost, elapsed_delta=total_elapsed
            )

        # 8. Quality checks
        actual_cost = max(engine_cost, float(result.cost_usd or 0.0))
        total_cost += actual_cost
        total_elapsed += float(result.elapsed_ms)
        is_empty = result.is_empty()
        is_blocked = is_empty or is_suspicious(result)
        if session is not None and proxy_pool is not None:
            proxy_pool.report(session, blocked=is_blocked)
        if is_blocked:
            reason = "empty" if is_empty else "suspicious"
            if (
                use_rotation
                and rotation < max_rotations
                and proxy_pool is not None
                and proxy_pool.usable()
            ):
                rotation += 1
                logger.debug("router: rotate exit IP after %s engine=%s", reason, canonical)
                continue
            failures.append(f"{canonical}:{reason}")
            return _AttemptResult(
                result=None, keep_walking=True, cost_delta=total_cost, elapsed_delta=total_elapsed
            )

        # 9. Winner
        return _AttemptResult(
            result=result, keep_walking=False, cost_delta=total_cost, elapsed_delta=total_elapsed
        )

    # Rotations exhausted (or pool drained mid-walk) without a good result —
    # escalate to the next tier rather than giving up the whole walk.
    if rotation > 0:
        failures.append(f"{canonical}:proxy_rotations_exhausted")
    return _AttemptResult(
        result=None, keep_walking=True, cost_delta=total_cost, elapsed_delta=total_elapsed
    )


# ---------------------------------------------------------------------------
# Public walk entry point
# ---------------------------------------------------------------------------


async def walk(
    url: str,
    opts: ScrapeOptions | None = None,
    pool: EnginePool | None = None,
) -> ScrapeResult:
    """Walk the resolved ladder and return the first non-empty result.

    If ``opts.engines`` is non-empty, those engine names are tried in order
    instead of the default per-site-class ladder.  Duplicate names (by
    canonical ``engine_cls.NAME``) are skipped after the first attempt.

    Raises ``AllEnginesFailed`` if every step fails or is skipped.

    Post-0.2 ladder walk: both ``SequentialStep`` and ``RaceStep`` entries
    are walked sequentially (one engine at a time). Concurrent fan-out with
    first-good-wins cancellation is deferred to a future router cycle.

    Parameters
    ----------
    pool:
        Caller-owned :class:`~scrapefold.pool.EnginePool`.  When ``None``
        (default), an ephemeral pool is created and closed after the walk
        regardless of outcome.  When provided, the caller is responsible for
        calling ``pool.aclose()``.
    """
    _caller_owns_pool = pool is not None
    if pool is None:
        pool = EnginePool()
    opts = opts or ScrapeOptions()
    try:
        return await _walk_inner(url, opts, pool)
    finally:
        if not _caller_owns_pool:
            await pool.aclose()


async def _walk_inner(
    url: str,
    opts: ScrapeOptions,
    pool: EnginePool,
) -> ScrapeResult:
    """Internal walk implementation; pool lifecycle managed by caller (``walk``)."""
    failures: list[str] = []
    # Proxy rotation layer ("proxy over proxy"): None unless the caller supplied
    # opts.proxies or a pre-built extra["proxy_pool"]. Shared across the walk so a
    # session retired by one engine stays retired for the rest of the walk.
    proxy_pool = _resolve_session_pool(opts)

    # -----------------------------------------------------------------------
    # opts.engines override path
    # -----------------------------------------------------------------------
    if opts.engines:
        site_class_override = classify_url(url)
        policy_override = _resolve_policy(opts, site_class_override)
        max_cost_usd_override = float(opts.extra.get("max_cost_usd", _DEFAULT_MAX_COST_USD))
        max_engines_override = int(opts.extra.get("max_engines", _DEFAULT_MAX_ENGINES))

        cost_accum: float = 0.0
        elapsed_accum_ms: float = 0.0
        # budget_tried: engines actually invoked (counts toward max_engines).
        # dedup_seen: all canonicals encountered (prevents re-attempting unavailable ones).
        budget_tried: set[str] = set()
        dedup_seen: set[str] = set()

        for name in opts.engines:
            # Elapsed-time budget (checked before any engine work).
            if elapsed_accum_ms >= opts.timeout_s * 1000:
                logger.info("router: walk halted budget=timeout (override path)")
                failures.append("budget:timeout")
                break

            # Dedup: emit :duplicate tag and skip before calling the helper.
            # We resolve the canonical name here so the tag is accurate.
            try:
                _cls = get_engine(name)
                _canon = _cls.NAME
            except KeyError:
                _canon = name  # unknown — helper will record :unknown

            if _canon in dedup_seen:
                logger.debug("router: skip engine=%s reason=duplicate", _canon)
                failures.append(f"{_canon}:duplicate")
                continue

            attempt = await _attempt_engine(
                engine_name=name,
                url=url,
                opts=opts,
                policy=policy_override,
                budget_cost_usd=cost_accum,
                budget_engines_tried=budget_tried,
                dedup_seen=dedup_seen,
                max_engines=max_engines_override,
                max_cost_usd=max_cost_usd_override,
                failures=failures,
                proxy_pool=proxy_pool,
            )

            cost_accum += attempt.cost_delta
            elapsed_accum_ms += attempt.elapsed_delta

            if attempt.result is not None:
                return replace(attempt.result, failures=failures)
            if not attempt.keep_walking:
                break

        raise AllEnginesFailed(url=url, failures=failures)

    # -----------------------------------------------------------------------
    # Default ladder path
    # -----------------------------------------------------------------------
    site_class = classify_url(url)
    policy = _resolve_policy(opts, site_class)
    ladder = get_ladder(site_class)
    budget = WalkBudget(visited_site_classes={site_class})
    # Separate dedup set so unavailable engines don't consume budget slots.
    # budget.engines_tried = actually invoked; ladder_dedup_seen = all encountered.
    ladder_dedup_seen: set[str] = set()

    max_engines = int(opts.extra.get("max_engines", _DEFAULT_MAX_ENGINES))
    max_cost_usd = float(opts.extra.get("max_cost_usd", _DEFAULT_MAX_COST_USD))

    for step in ladder:
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

        for engine_name in engines_to_try:
            # Elapsed-time budget check (per-engine, mirrors override path).
            if budget.elapsed_ms >= opts.timeout_s * 1000:
                logger.info("router: walk halted budget=timeout (ladder path)")
                failures.append("budget:timeout")
                raise AllEnginesFailed(url=url, failures=failures)

            attempt = await _attempt_engine(
                engine_name=engine_name,
                url=url,
                opts=opts,
                policy=policy,
                budget_cost_usd=budget.cost_usd,
                budget_engines_tried=budget.engines_tried,
                dedup_seen=ladder_dedup_seen,
                max_engines=max_engines,
                max_cost_usd=max_cost_usd,
                failures=failures,
                proxy_pool=proxy_pool,
            )

            budget.cost_usd += attempt.cost_delta
            budget.elapsed_ms += int(attempt.elapsed_delta)

            if attempt.result is not None:
                return replace(attempt.result, failures=failures)
            if not attempt.keep_walking:
                raise AllEnginesFailed(url=url, failures=failures)

    raise AllEnginesFailed(url=url, failures=failures)


__all__ = ["walk"]
