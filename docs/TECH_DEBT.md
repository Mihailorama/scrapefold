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

## P1 — router-coupled items

These seven items came out of Codex round-3 review of the ladders PR
(`agentId: ae2836a633272deb6`). The structural design was approved
(Option A); these are implementation pin-downs that belong in code, not
in the ladders.py file alone.

**v0.1.0 release split:**

- v0.1.0 ships **sequential-only** — `RaceStep` entries in ladders are
  skipped with a DEBUG log. Items below tagged "sequential-relevant"
  land before the v0.1.0 tag (Pack 3 = router shell, Pack 4 = router
  cost accounting + probe cache, Pack 5 = engine pool + client reuse).
- Items tagged "RaceStep-coupled" defer to v0.2.0 — they cannot be
  validated without race fan-out in the router. Item #1
  (`budget_mode`), #2 (race billing), and #4 (race billing default)
  are RaceStep-coupled.

This split is non-negotiable per consumer-driven scope: known
downstream consumers only need sequential walks at v0.1.0 ship time.

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

### 8. Per-instance httpx client in HTTP-tier engines

- **Where:** `engines/requests.py`, `engines/jina.py`, `engines/scrapingdog.py`, `engines/anysite.py`.
- **Status:** Each `_fetch` opens a fresh `httpx.AsyncClient`. For a 50-URL crawl that's 50 TLS handshakes + connection-pool teardowns. Flagged by Wave 2 Pack 2A efficiency review; anysite added in Pack 2B.
- **Fix sketch:** instantiate `self._client: httpx.AsyncClient` once in `__init__`, and add an `async def aclose(self)` method the router (S7) calls at walk shutdown.
- **Why deferred:** router doesn't exist yet, so there's no consumer to call `aclose()`. Adding a per-call `client.aclose()` would defeat the optimization. Land alongside S7.
- **Test:** `test_engine_reuses_client_across_calls` — instantiate engine, scrape twice, assert the same `httpx.AsyncClient` instance was used.

### 9. SDK client reuse across calls (Firecrawl, Apify, Outscraper)

- **Where:** `engines/firecrawl.py:_fetch_scrape`, `engines/apify_linkedin.py:_fetch`, `engines/outscraper.py:_fetch`.
- **Status:** Each `_fetch` rebuilds the vendor SDK client. Firecrawl's `AsyncFirecrawlApp` opens an httpx pool per instance; Apify's `ApifyClientAsync` opens an aiohttp `ClientSession`; Outscraper's `ApiClient` constructs a `requests.Session`. Per-call teardown wastes the pool.
- **Fix sketch:** cache the SDK instance on `self` after first `_fetch`. Outscraper's `requests.Session` is also a candidate for `aclose()`-style cleanup at walk shutdown.
- **Land:** S7 router work or sooner if benchmarks show it.

### 10. Sitemap / robots / BFS discovery does not escalate

- **Where:** `src/scrapefold/crawler/sitemap.py`, `src/scrapefold/crawler/__init__.py`.
- **Status:** `crawl_site()` correctly escalates engines on **per-URL scrapes**, but the upstream **URL discovery** (sitemap.xml, robots.txt, BFS link-extraction) uses an internal `httpx.AsyncClient` — same path as the `requests` engine. On a Cloudflare/anti-bot protected site, the homepage scrape succeeds via `scrapling_stealth` but the sitemap fetch fails with a block page → discovery returns `[root]` only → crawl_site produces 1 page instead of N.
- **Symptom (found via smoke test on 2026-05-25):** crawls on Cloudflare-protected targets return 1 page (homepage only) even though the per-page ladder works. Sitemap.xml fetch returns the same 403 / block content as the homepage but is parsed as empty XML → BFS fallback fires but can't even reach the root document.
- **Fix sketch:** route sitemap.xml / robots.txt / BFS-discovery fetches through `scrapefold.scrape(url, opts)` instead of a hard-coded `httpx.AsyncClient.get()`. The result's `markdown`/`html` can be parsed for sitemap XML or BFS links. Costs more (BFS discovery now goes through ladder for every discovered page) but is the only way to crawl protected sites.
- **Test:** `test_crawl_site_uses_engine_ladder_for_sitemap_fetch` — mock the requests engine to return 403, mock scrapling_stealth to return valid sitemap.xml; assert sitemap.xml URLs are discovered.
- **Priority:** P2 (deferred to v0.2 alongside parallel fan-out). Consumers can work around for v0.1.0 by passing pre-discovered URL lists for protected targets.

### 11. No residential-proxy engine for IP-geofenced targets

- **Where:** `src/scrapefold/engines/`.
- **Status:** Some geofenced targets are unreachable from US/EU IPs at the network layer (TCP timeout, not bot detection). No amount of stealth / JS rendering / fingerprint randomisation helps — the connection never completes. To scrape such targets, scrapefold needs a residential-proxy engine that routes requests through a geo-matched exit node.
- **Originally scoped, never shipped:** `brightdata_unlocker` and `brightdata_browser` were listed in the early README's 16-engine table; the placeholder pyproject extras were removed in `0.1.0a2` (CHANGELOG).
- **Fix sketch:** implement `engines/brightdata_unlocker.py` against Bright Data's Web Unlocker API (`https://api.brightdata.com/datacenter/zone/unlock` or equivalent). Capability: `proxy_type="residential"`, `geography=(<country_code>,)`. Wire into ladders for the geofenced site class.
- **Priority:** P2 — needed for full coverage of IP-geofenced targets but workable around for v0.1.0 (the vast majority of targets are reachable without).

## How to add an item

1. Open a row here with **P-priority**, **where** (file/function), **status**, **fix sketch**, **test**.
2. Link the originating PR or Codex review `agentId`.
3. Move the item to `CHANGELOG.md` § Changed when shipped, deleting the row here.
