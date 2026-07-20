---
purpose: "Prioritized register of known follow-up items deferred from earlier PRs."
updated: "2026-07-09"
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

**Post-0.3 status:**

- v0.3.0 shipped the social-normalization layer, `apify_actor`, Telegram,
  TGStat, Telemetr, LabelUp, PixelRAG, and SocialCrawl. The router still
  walks `RaceStep` entries sequentially.
- Items tagged "RaceStep-coupled" remain open after v0.3.0 — they cannot
  be fully validated without concurrent race fan-out in the router. Item
  #1 (`budget_mode`), #2 (race billing), and #4 (race billing default)
  are RaceStep-coupled.

Sequential walking is still the consumer-safe default; concurrent fan-out
needs a focused router cycle with billing and budget tests before it becomes
public behavior.

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

### 10. Sitemap / robots / BFS discovery does not escalate — RESOLVED (v0.1.1)

- **Where:** `src/scrapefold/crawler/sitemap.py`, `src/scrapefold/crawler/__init__.py`.
- **Resolution:** `discover_urls()` now accepts a `fetcher: DiscoveryFetcher` parameter. When `None` (the default for direct callers and unit tests), the legacy httpx-based path is used. When called from `crawler.crawl()`, an engine-aware fetcher built by `_make_engine_aware_fetcher(crawl_opts, pool)` wraps `scrapefold.scrape()` so sitemap.xml / robots.txt / BFS pages are fetched through the full engine ladder. A Cloudflare-protected sitemap that returns 403 to the `requests` engine now escalates to `scrapling_stealth` (or higher) and discovery yields real URLs.
- **Tests:** `tests/test_crawler_sitemap.py::TestDiscoveryFetcher` (three unit tests covering the fetcher contract) and `tests/test_crawl_site.py::test_crawl_site_discovers_via_engine_ladder` (integration test asserting `scrapefold.scrape` is invoked for `sitemap.xml`).

### 11. Dedicated geofenced fallback tier

- **Where:** `src/scrapefold/engines/`.
- **Status:** Some geofenced targets are unreachable from US/EU IPs at the network layer (TCP timeout, not bot detection). No amount of stealth / JS rendering / fingerprint randomisation helps — the connection never completes. `oxylabs` ships residential geo routing (`ScrapeOptions.country` → `geo_location`), but the dedicated Bright Data-family fallback tier is still missing after v0.3.0.
- **Originally scoped, never shipped:** `brightdata_unlocker` and `brightdata_browser` were listed in the early README's 16-engine table; the placeholder pyproject extras were removed in `0.1.0a2` (CHANGELOG).
- **Fix sketch:** implement `engines/brightdata_unlocker.py` against Bright Data's Web Unlocker API (`https://api.brightdata.com/datacenter/zone/unlock` or equivalent). Capability: `proxy_type="residential"`, `geography=(<country_code>,)`. Wire into ladders for the geofenced site class.
- **Priority:** P2 — needed for provider redundancy and full coverage of IP-geofenced targets; Oxylabs covers the first shipped residential-geo path.

### 12. No sync wrapper that's robust to leaked event loops in the caller

- **Where:** `src/scrapefold/__init__.py` (public API surface).
- **Status:** `scrape()` is async-only; callers in sync codebases write
  `asyncio.run(scrape(...))` per call. This breaks the moment the caller's
  process has *any* leaked asyncio loop in its main thread — most common
  trigger: Playwright Sync API (used by `cloakbrowser`, `playwright-stealth`,
  etc.) leaks a running loop, after which `asyncio.run` raises
  `RuntimeError: asyncio.run() cannot be called from a running event loop`.
- **Found by:** phynder PR 1 smoke test on 2026-05-26 (`Mihailorama/phynder#62`,
  commit `ed16868`). Phynder's adapter currently works around this by running
  `asyncio.run(scrape(...))` inside a `ThreadPoolExecutor(max_workers=1)` —
  the worker thread always has a clean asyncio context regardless of leaks
  in the main thread.
- **Fix sketch:** expose `scrape_sync(url, opts=None, pool=None) -> ScrapeResult`
  alongside `scrape()`. Implementation does the same worker-thread isolation
  internally so sync callers don't repeat the pattern. Same for `crawl_site_sync`.
  Document the rationale (leaked loops from Playwright Sync) in the docstring.
- **Why P2:** sync callers can write the 5-line `ThreadPoolExecutor` workaround
  themselves (phynder did), so it's an ergonomics improvement, not a blocker.
  Becomes more valuable as more sync codebases adopt scrapefold.
- **Test:** `test_scrape_sync_works_inside_running_event_loop` — call
  `scrape_sync()` from inside `asyncio.run(harness())` and assert it returns
  a `ScrapeResult` instead of raising.

### 13. Maxun capability ratings in README / site are unverified

- **Where:** `README.md` (Engine Comparison + Supported Engines tables), `docs/index.html` (engine card).
- **Status:** The `maxun` engine (v0.4.0) was merged without any README/site
  documentation. When the 0.4.0 release was cut (with the `serper` engine),
  maxun was added to the tables/site to keep the "27 engines" count honest,
  but its capability stars (`Static ★★★ / JS ★★★ / Stealth ★★☆ / Medium /
  Free`) were **inferred from its CHANGELOG description**, not measured.
- **Fix:** verify against a live self-hosted Maxun instance (robot replay is
  Playwright-based) and correct the ratings / strengths blurb if wrong.
- **Priority:** P3 — cosmetic doc accuracy, no code impact.

## P2 — architecture borrowed from competitors (carousel review 2026-07-18)

These three came out of a side-by-side with the market leaders (Firecrawl,
Crawl4AI, Crawlee, ScrapeGraphAI, Scrapy, pydoll, Camoufox, Trafilatura,
Katana, Selectolax). The engines worth having as engines shipped this cycle
(`pydoll`, `camoufox`, plus Trafilatura as opt-in `main_content`). What's left
are **cross-cutting architecture ideas** — layers, not engines — that the
per-engine `ScrapeEngine` abstraction doesn't cover yet. Each is scoped to slot
in without breaking the golden rules (one options schema, engines drop
unsupported opts, no vendor LLM SDK).

### 14. Crawlee-style session pool + proxy rotation ("proxy over proxy") — RESOLVED (Unreleased)

- **Where:** `src/scrapefold/proxy.py` (`Session` + `SessionPool` +
  `build_pool_from_options`), consumed by `router.walk`; new
  `ScrapeOptions.proxy` / `ScrapeOptions.proxies` / `extra["proxy_pool"]`.
- **Resolution:** a health-scored `SessionPool` now sits *above* each engine's
  single-proxy setting — the "прокси над прокси" layer.
  `_resolve_session_pool(opts)` builds it from `opts.proxies` (per-walk) or a
  caller-owned `extra["proxy_pool"]` (crawl-spanning). Inside `_attempt_engine`,
  a proxy-capable engine (`"proxy" in SUPPORTED_OPTIONS`) that returns a blocked
  / suspicious / errored response has its session struck
  (`pool.report(session, blocked=…)`) and is **retried behind a fresh exit IP**
  (up to `extra["proxy_max_rotations"]`, default 2) before the walk escalates to
  the next tier. Sessions retire past `max_errors` (default 3, Crawlee-style)
  and heal one strike on a clean response; `acquire()` hands out the healthiest,
  least-used exit. The unified `ScrapeOptions.proxy` maps to each free stealth
  engine's native option (camoufox `proxy` dict, pydoll `--proxy-server`,
  scrapling `proxy`); vendor engines that run their own fleet simply drop it.
  The engine abstraction is untouched (engines still take one proxy) and the
  whole layer is opt-in — with no proxies configured the router path is
  byte-for-byte unchanged. Pool exhaustion never silently falls back to the real
  IP: the engine is skipped so the walk escalates to vendor unlockers instead.
- **Tests:** `tests/test_proxy.py` (12 unit tests: dedup, health-order acquire,
  strike/retire/heal, exhaustion, masking) and
  `tests/test_router.py::test_session_pool_retires_blocked_session_and_rotates_exit_ip`
  (two proxies; first blocks → second chosen on retry; blocked one retired and
  not reused), plus rotation-give-up and no-rotation-without-proxy-support, and
  per-engine `proxy`-mapping tests for camoufox / pydoll / scrapling_stealth.

### 15. Scrapy-style AutoThrottle for the crawler

- **Where:** `src/scrapefold/crawler/__init__.py` (the BFS crawl loop); new
  `ScrapeOptions.autothrottle` / `extra["target_concurrency"]`.
- **Status:** `crawl()` walks discovered URLs but uses a fixed fan-out. [Scrapy](https://github.com/scrapy/scrapy)'s
  AutoThrottle adapts the request delay/concurrency to the *observed* server
  latency — speeding up on healthy hosts, backing off when a host slows or
  starts returning errors. scrapefold has neither adaptive delay nor a
  politeness backoff, so a large crawl can hammer a slow origin (and invite
  blocks, undercutting the stealth engines it just added).
- **Fix sketch:** track an EWMA of per-host response latency + error rate in
  the crawl loop; scale the in-flight semaphore between
  `[1, max_concurrency]` toward a target latency, and add exponential backoff
  on 429/503. Pure crawler-layer change; engines and `ScrapeResult` untouched.
- **Why P2:** improves crawl robustness + politeness, but single-URL `scrape()`
  is unaffected and current crawls work.
- **Test:** `test_autothrottle_backs_off_on_rising_latency` (feed synthetic
  latencies; assert effective concurrency drops).

### 16. LLM-schema extraction (ScrapeGraphAI-shaped) via the user LLM callable

- **Where:** `src/scrapefold/` — a new `extract(result, schema, llm=...)` helper
  built on the **existing** user-provided async LLM callable (same contract as
  `llm_judge` / `vision.py`).
- **Status:** [ScrapeGraphAI](https://github.com/ScrapeGraphAI/Scrapegraph-ai)'s
  hook is "describe what you want in natural language, the LLM builds the
  extraction." scrapefold deliberately ships **no vendor LLM SDK** (golden rule
  #5), so we can't adopt its engine — but the *shape* is compatible: feed
  `result.markdown` + a user schema to the caller's own LLM callable and return
  structured `json`. Firecrawl `/extract` already lands structured data in
  `ScrapeResult.json`; this generalizes it to any engine's output without
  importing `openai`/`litellm`.
- **Fix sketch:** `async def extract(result, *, schema, llm) -> dict` that
  prompts the injected `llm` with the markdown + JSON schema and validates the
  return into `result.json`. Provider-agnostic; no new dependency.
- **Why P2 / careful:** must stay a thin helper over the user callable — the
  moment it imports a provider SDK it violates rule #5. Lower priority than the
  proxy/throttle layers, which have no LLM dependency.
- **Test:** `test_extract_fills_json_via_injected_llm` (stub llm returns a
  fixed JSON blob; assert it lands in `result.json`, and that no vendor LLM
  module is imported).

### Deliberately NOT adopted

Recorded so the next reviewer doesn't re-litigate:

- **Crawlee (framework), Scrapy (framework):** whole crawling *frameworks* with
  their own engine/scheduler/pipeline model — adopting either wholesale would
  replace scrapefold's router+ladder, not extend it. We borrow their best
  *ideas* (items 14–15) instead. Crawlee is also Node.js.
- **Katana:** a Go link/endpoint-discovery crawler for pentest recon — it emits
  URLs, not cleaned content, so it's out of scope for a URL→markdown library.
  (If sitemap/BFS discovery ever needs a JS-aware endpoint harvester, revisit.)
- **Selectolax:** a faster HTML parser (Lexbor). Pure optimization — would swap
  BeautifulSoup/markdownify in `html_to_text.py` for throughput on massive
  crawls. Deferred until parsing shows up in a profile; not a capability gap.

## How to add an item

1. Open a row here with **P-priority**, **where** (file/function), **status**, **fix sketch**, **test**.
2. Link the originating PR or Codex review `agentId`.
3. Move the item to `CHANGELOG.md` § Changed when shipped, deleting the row here.
