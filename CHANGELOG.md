# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0a1] — 2026-05-22

### Added — S1.5 per-site-class escalation ladders

- `src/scrapefold/ladders.py` — full v3 design after three Codex review rounds.
  - `SiteClass` literal with 27 classes (LinkedIn ×5, Amazon ×2, social ×4, SERP ×3, easy content ×4, paywall_news, yandex_protected, anti-bot vendor ×4, js_spa, static_general).
  - Sum type for steps: `SequentialStep` + `RaceStep` (no more weak tuple of tuples).
    - `RaceStep` carries explicit `winner_policy`, `cancel_policy`, `cancel_grace_ms`, `budget_accounting` — race semantics are data, not router convention.
  - `LADDERS: dict[SiteClass, Ladder]` — per-class ordered tuple of steps.
  - `WalkBudget` — mutable walk state (elapsed_ms, cost_usd, engines_tried, **visited_site_classes** to block A→B→A reclassification loops, reclassifications, `MAX_RECLASSIFICATIONS=3`).
  - `BudgetExceeded` + `AllEnginesFailed` — typed exceptions surfaced to the router.
  - `Policy` + `DEFAULT_POLICY` — `paid_allowed`, `legal_constraints_blocked`, `geography_required`. Government class defaults to `paid_allowed=False`.
  - `is_step_allowed(step, policy)` and `check_budget(step, walk, ...)` — pure functions consumed by the router. `check_budget` accounts for **race fan-out** in the engine-count ceiling (Codex round-3 fix).
  - `_estimate_step_cost(step, avg_response_mb)` — converts `(estimated_cost_usd, billing_unit)` into per-call USD; `gb` billing scales with response size.
  - `URL_PATTERNS` + `classify_url` — ordered specific-first regex table, fallback `static_general`.
  - `GOLDEN_CORPUS` — 22 `(url, class)` rows pinning regex-order safety. The parametrized test `test_url_classification_golden_corpus` makes any reorder regression visible per-row.
  - `SIGNATURES` — response-content matchers (Cloudflare / Datadome / PerimeterX / Akamai cookies, body phrases, headers) for mid-walk reclassification by `detection.py` (S2). `min_matches=2` prevents single-phrase false positives.
- `src/scrapefold/engines/base.py` — extended `EngineCapabilities` with `estimated_cost_usd`, `billing_unit`, `avg_response_mb_estimate`, `geography`, `proxy_type`, `legal_constraints`, `default_timeout_s`. Added `PROBE_SCOPE: Literal["none", "per_url", "per_domain", "per_session"]` and default `async def probe(url) -> bool` returning `True`. Reddit's requests-engine override (`PROBE_SCOPE="per_domain"`) means a 50-URL crawl on reddit.com costs one probe, not 50.
- `src/scrapefold/engines/__init__.py` — `ENGINE_ALIASES`, `register_alias`, `resolve_alias`. Multi-mode engines (Bright Data Unlocker async/sync, Scrapling stealth/fast) register as distinct names; user-facing aliases route a bare `scrapling` to `scrapling_stealth`.
- `src/scrapefold/__init__.py` — re-exports `Policy`, `SequentialStep`, `RaceStep`, `WalkBudget`, `SiteClass`, `BudgetExceeded`, `AllEnginesFailed`, `classify_url`, `get_ladder`. Version bumped to `0.1.0a1`.
- `tests/test_ladders.py` — 59 tests covering structural well-formedness, 22-row golden corpus + completeness, `is_step_allowed` policy enforcement (paid/legal/geography), `check_budget` ceilings + race fan-out, sum-type defaults, multi-mode engine separation, `WalkBudget` visited-class loop guard, `_estimate_step_cost` billing-unit math.

### Changed

- Architecture overview (`docs/architecture/overview.md`) — replaced universal T0-T5 ladder narrative with per-class ladder description, race-step semantics, walk-time contracts.
- Golden rules (`docs/conventions/golden-rules.md`) — rewrote the escalation rule, added three new rules: "Ladders are data", "Multi-mode engines register as distinct names", "New URL pattern → new GOLDEN_CORPUS row".

### Deferred

Seven Codex round-3 implementation items tracked in `docs/TECH_DEBT.md`:

1. `budget_mode` wiring in the router (S7).
2. Race fan-out cost crediting when `budget_accounting="sum_all"`.
3. Per-engine `avg_response_mb` override wiring through `_estimate_step_cost`.
4. Race billing default re-examination once benchmarks land.
5. `avg_response_mb_estimate` default tuning per engine.
6. Engine-registration code must populate `ENGINE_ALIASES` (S2-S11).
7. Probe-cache implementation in the router (S7).

## [0.1.0a0] — 2026-05-22

### Added — S1 scaffold

- Package layout: `src/scrapefold/` with `options.py`, `result.py`, `engines/base.py`, lazy engine registry.
- `ScrapeOptions` dataclass — single unified parameter schema (language, country, render_js, wait_ms, stealth, premium_proxy, user_agent, custom_headers, cookies, output_format, take_screenshot, max_pages, max_depth, engines, parallel, timeout_s, skip_cache, extra).
- `ScrapeResult` dataclass — four format slots (`text`, `markdown`, `html`, `json`) + `screenshot_b64`, `meta`, `failures`, `elapsed_ms`, `cost_usd`.
- `ScrapeEngine` ABC with `EngineCapabilities` + `EngineError`. Base class handles option-stripping (DEBUG log, never raises), timing, error wrapping.
- Public `scrape()` and `crawl_site()` stubs in `__init__.py` (raise `NotImplementedError` until S7/S8).
- CLI scaffold (`scrapefold` console script via Typer).
- MCP server scaffold (`scrapefold-mcp` console script).
- Docs graph per HARNESS_BOOTSTRAP: `docs/README.md`, `docs/architecture/overview.md`, `docs/workflows/{development,testing}.md`, `docs/conventions/golden-rules.md`, `docs/tools/{agent-mode,scripts}.md`.
- Anti-bot escalation ladder documented (T0→T4 + stop rules + suspicious-content detection heuristics planned in `detection.py`).
- Golden rules: unified options, options-dropping-never-raises, ScrapeResult invariant (all four slots), escalate-and-stop, suspicious-detection-in-one-place, no vendor LLM SDK, lazy engine imports, async everywhere.
- Helper scripts: `scripts/check.sh`, `scripts/describe.sh`, `scripts/quick-test.sh`.
- GitHub Actions CI: lint + type-check + offline tests on Python 3.10/3.11/3.12; PyPI publish via trusted publishing on `v*` tag; opt-in `paid` and `network` test jobs via `workflow_dispatch`.
- Smoke tests + `ScrapeEngine` ABC contract tests.

[Unreleased]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a0...v0.1.0a1
[0.1.0a0]: https://github.com/mihailorama/scrapefold/releases/tag/v0.1.0a0
