---
purpose: "Prioritized register of known follow-up items deferred from earlier PRs."
updated: "2026-05-22"
related:
  - architecture/overview.md
  - ../CHANGELOG.md
---

# Technical debt register

Items here are known shortcuts that were accepted in earlier PRs and need
follow-up in later sprints. Each item has a P-priority, the PR that
introduced it, and the sprint where it should land.

## P1 — block S7 (router) or land alongside it

These seven items came out of Codex round-3 review of the ladders PR
(`agentId: ae2836a633272deb6`). The structural design was approved
(Option A); these are implementation pin-downs that belong in code, not
in the ladders.py file alone.

### 1. `budget_mode` wiring in the router

- **Where:** `src/scrapefold/router.py` (S7).
- **Status:** `BudgetMode = Literal["inherit", "reset_user_fast_track", "reset_fresh_session"]` is defined and `_StepBase.budget_mode` carries the field, but no code mutates `WalkBudget` when a step declares a non-`inherit` mode.
- **Fix:** add `_apply_budget_mode(walk, step.budget_mode)` at the top of the router loop. `reset_user_fast_track` zeros `cost_usd`/`engines_tried` but keeps `reclassifications`; `reset_fresh_session` zeros everything.
- **Test:** `test_router_applies_reset_fresh_session` once router lands.

### 2. Race fan-out cost when `budget_accounting="sum_all"`

- **Where:** `src/scrapefold/router.py` (S7), `_invoke_race_step`.
- **Status:** `RaceStep.budget_accounting` is data; ladders already opt paid races into `sum_all`. But the router accumulator that credits `WalkBudget.cost_usd` doesn't yet exist.
- **Fix:** for each engine that actually issued a request inside the race, add its `_estimate_step_cost`-equivalent to `walk.cost_usd`. `winner_only` only counts the winner; `max` charges the most expensive engine that ran.
- **Test:** `test_router_race_step_bills_all_engines_when_sum_all`.

### 3. Per-engine `avg_response_mb` override

- **Where:** `src/scrapefold/router.py` calling `_estimate_step_cost`.
- **Status:** `EngineCapabilities.avg_response_mb_estimate: float = 2.0` exists per engine. `_estimate_step_cost` accepts an `avg_response_mb` arg but the router doesn't read the engine cap and pass it through.
- **Fix:** when computing step cost for a `SequentialStep`, look up the engine's `CAPABILITIES.avg_response_mb_estimate` and pass it. For a `RaceStep`, take the max across the racing engines.
- **Test:** `test_estimate_step_cost_for_browser_engine_uses_higher_mb_estimate`.

### 4. Race billing default re-examination

- **Where:** `src/scrapefold/ladders.py` `RaceStep.budget_accounting` default.
- **Status:** Current default is `winner_only`. Paid races in LADDERS already opt into `sum_all`. Codex round-3 R3-H1 argued the default should be `sum_all` because most paid vendors bill failed attempts.
- **Fix:** once a `bills_failed_attempts: bool` lands on `EngineCapabilities`, derive the default per RaceStep from the engines it lists rather than hardcoding either side. Until then, keep `winner_only` default + per-step opt-in.
- **Test:** golden-corpus snapshot of every `LADDERS` entry's billing mode (already partially covered by `test_paid_linkedin_race_steps_use_sum_all_billing`).

### 5. `avg_response_mb_estimate` default per engine

- **Where:** Each engine file under `src/scrapefold/engines/`.
- **Status:** Base default `2.0` is conservative for static HTTP, low for browser/unlocker engines that easily push 10-50 MB per session. The field exists; per-engine overrides are not yet set because engines don't exist yet.
- **Fix:** when each engine PR (S2-S11) lands, set `CAPABILITIES = EngineCapabilities(avg_response_mb_estimate=N, ...)`. Browser/unlocker engines: 15-30 MB. Markdown engines (Jina): 0.5 MB.
- **Test:** add a per-engine smoke test asserting the value is non-default for browser engines.

### 6. Engine-registration must populate `ENGINE_ALIASES`

- **Where:** Each engine file under `src/scrapefold/engines/` for multi-mode engines.
- **Status:** `register_alias` / `resolve_alias` exist; `ENGINE_ALIASES` ships empty. Each engine PR for a multi-mode engine must call `register_alias("scrapling", "scrapling_stealth")` (etc.) at module import time.
- **Fix:** in `engines/scrapling_stealth.py`, `engines/brightdata_unlocker_sync.py`, add a `register_alias(...)` call alongside `register(...)`.
- **Test:** `test_user_facing_alias_resolves_to_default_mode` per engine.

### 7. Probe-cache implementation in the router

- **Where:** `src/scrapefold/router.py` (S7).
- **Status:** `ScrapeEngine.PROBE_SCOPE` declared (`"none" | "per_url" | "per_domain" | "per_session"`), default `probe()` returns `True`. No cache exists yet.
- **Fix:** module-level `dict[tuple[engine_name, scope_key], bool]` where `scope_key` is `url`, `tldextract.extract(url).registered_domain`, or `"_session"`. Look up before calling `engine.probe(url)`; store the result keyed by scope.
- **Test:** `test_router_caches_per_domain_probe_across_urls` (50-URL crawl on reddit.com → 1 probe call).

## P2 — backlog (no current blocker)

None at the time of writing — re-evaluate after S7 lands.

## How to add an item

1. Open a row here with **P-priority**, **where** (file/function), **status**, **fix sketch**, **test**.
2. Link the originating PR or Codex review `agentId`.
3. Move the item to `CHANGELOG.md` § Changed when shipped, deleting the row here.
