# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [SemVer](https://semver.org/).

## [Unreleased]

### Added

- **MCP server implemented** (`scrapefold-mcp`) — the console-script scaffold
  is now a real stdio server built on the official `mcp` SDK (FastMCP), with
  four tools: `scrape_url` (single URL → `ScrapeResult` JSON, with
  `engines`/`render_js`/`stealth` args), `crawl_site` (whole site → stitched
  markdown), `list_engines`, and `classify_url`. Payloads stay LLM-friendly:
  `screenshot_b64` is dropped and oversized `html` is nulled. Without the
  `mcp` extra the entry point exits 2 with an install hint.
- **One-click MCP registration** — new `scrapefold install <client>` CLI
  command (`claude` / `codex` / `cursor` / `vscode` / `generic`): invokes the
  client's own registration CLI when on PATH (`claude mcp add …`,
  `codex mcp add …`, `code --add-mcp …`), merges `~/.cursor/mcp.json` in
  place for Cursor, or prints the standard `mcpServers` JSON. `--print-only`
  previews, `--json` emits the config alone. Logic lives in
  `scrapefold/install.py` (pure, unit-tested planning + JSON merge).
- **`scrapefold update`** — self-update command: `--check` compares the
  installed version against the latest PyPI release (`--json` for agents),
  the default action upgrades via the running interpreter's pip, with
  `--extras "mcp,firecrawl"` to keep extras in the requirement.
- **Structured MCP failures + token budget** — `scrape_url` returns
  `{"url", "error": "all engines failed", "failures": [...]}` instead of a
  raw exception when the ladder is exhausted; a budget test pins all four
  tool definitions + instructions at ≈750 estimated tokens so they stay
  agent-cheap.
- **Wayback engine** (`wayback`) — free, keyless dead-link recovery via the
  Internet Archive: availability-API lookup → raw (`id_`) snapshot fetch →
  markdown, with the result honestly marked in the `json` slot
  (`{"source": "archive.org", "snapshot_timestamp", "snapshot_url"}`) and
  `meta["archived"] = True`. Pin a date with `extra["wayback_timestamp"]`.
  Raises a clean `EngineError` when no snapshot exists — never fake content.
- **Focused extraction** (`--focus` / MCP `scrape_url(focus=...)`) — BM25
  block filtering of the final markdown (stdlib-only): keeps only blocks
  relevant to the query plus their governing headings, in page order, with
  `[...]` omission markers and a relative-score threshold; falls back to the
  full page when nothing matches. Saves most of the context tokens when an
  agent needs one fact from a long page.
- **`scrapefold doctor`** — post-install health check: version, Python,
  MCP-extra presence, and per-engine importability (`--json` for agents).
  Referenced as the verify step in the agent setup prompt.
- **Agent setup prompt page** — `docs/install.md`, served raw at
  `https://scrapefold.com/install.md` (new `docs/.nojekyll`), gives any AI
  agent a complete fetchable install instruction; `docs/llms.txt` indexes it.
  The landing page gained a here.now-style "Copy setup prompt for my agent"
  button (with a visible `fetch https://scrapefold.com/install.md` fallback
  line), an "One-click install for AI agents" section with an Add-to-Cursor
  deeplink, and a copyable MCP config JSON.

- **pydoll engine** (`pydoll`) — free, local, stealth-first Chromium driven
  directly over the Chrome DevTools Protocol with **no WebDriver** and no
  `navigator.webdriver` flag, built to clear Cloudflare / Turnstile challenges
  without plugins. Maps `user_agent`, `language`, `cookies` (injected before
  navigation), `wait_ms`, `wait_for_selector`, `take_screenshot`, `timeout_s`;
  extra raw Chrome flags via `extra["pydoll_args"]` and an explicit browser
  path via `extra["pydoll_binary"]` (for containers where auto-detection
  fails). Duplicate / pydoll-reserved flags (e.g. `--no-first-run`) are
  de-duplicated and never crash the scrape. Lazy-imported SDK
  (`pip install scrapefold[pydoll]`), `cost_usd=0.0`. Raced into the free
  stealth tier of the general, `js_spa`, `ecommerce_other`, and
  `cloudflare_protected` ladders.
- **Camoufox engine** (`camoufox`) — free, local anti-detect **Firefox** whose
  fingerprint is coherent at the browser source level, passing anti-bot walls
  that patched-Chromium engines leak on. scrapefold's only Firefox-based
  stealth path, giving the ladder fingerprint diversity a second Chromium can't.
  A drop-in Playwright browser: maps `language`→`locale` + `Accept-Language`,
  `custom_headers`, `cookies`, `wait_until`, `wait_ms`, `wait_for_selector`,
  `take_screenshot`, `timeout_s`; `camoufox_*` launch-option passthrough via
  `extra` (proxy, os, geoip, fingerprint). `user_agent` is intentionally
  dropped to keep the fingerprint coherent. Lazy-imported SDK
  (`pip install scrapefold[camoufox]` + `python -m camoufox fetch`),
  `cost_usd=0.0`. Raced alongside pydoll in the free stealth tier.
- **Main-content extraction** — `ScrapeOptions(main_content=True)` re-derives
  `ScrapeResult.text`/`.markdown` from the main article body (nav / ads /
  boilerplate stripped) via [Trafilatura](https://github.com/adbar/trafilatura),
  applied **centrally** in `ScrapeEngine.scrape` so every HTML-producing engine
  in a fallback chain honors it. Degrades gracefully: if `trafilatura` is not
  installed or finds no article, the engine's full-page output is kept
  unchanged (never blanks a result). New helper
  `scrapefold.html_to_text.html_to_main_content`; optional extra
  `pip install scrapefold[trafilatura]`.
- **Proxy rotation layer — "proxy over proxy"** (`scrapefold.proxy`). A
  health-scored `SessionPool` that sits *above* each engine's single-proxy
  setting: pass `ScrapeOptions(proxies=(...))` (or a crawl-spanning
  `extra["proxy_pool"]`) and, when a response looks blocked, the router retries
  the **same** engine behind a **different exit IP** before escalating to the
  next (more expensive) tier — the cheaper win for datacenter / residential
  fleets. Sessions accrue strikes on blocks (Crawlee-style), retire past a
  threshold (`max_errors`, default 3), and heal one strike on a clean response;
  rotation is capped per engine by `extra["proxy_max_rotations"]` (default 2).
  New unified `ScrapeOptions.proxy` maps to each proxy-capable engine's native
  option (Camoufox `proxy` dict, pydoll `--proxy-server`, scrapling `proxy`);
  vendor engines that manage their own fleet drop it. The engine abstraction is
  untouched — engines still take one proxy, the pool owns which. Fully opt-in:
  with no proxies configured, the router path is unchanged. Resolves
  TECH_DEBT #14.
- **AutoThrottle — adaptive crawl politeness** (`scrapefold.crawler.throttle`).
  A [Scrapy](https://github.com/scrapy/scrapy)-style per-host controller: set
  `ScrapeOptions(autothrottle=True)` and `crawl()` sleeps an adaptive delay
  before each page fetch, easing toward `latency / target_concurrency` from an
  EWMA of observed latency, **never speeding up** on a non-2xx response, and
  backing off hard (2×) on `429` / `503` or a hard fetch failure — clamped to
  `[min_delay, max_delay]`. Keeps a large crawl from hammering a slow or
  rate-limiting origin (and inviting the blocks the stealth engines were added
  to dodge). Tuning via `extra["autothrottle_target_concurrency" | "start_delay"
  | "max_delay" | "min_delay"]`. Pure crawler-layer, opt-in (default off →
  zero sleeps); engines, `ScrapeResult`, and single-URL `scrape()` untouched.
  Resolves TECH_DEBT #15.
- **LLM-schema extraction** (`scrapefold.extract`) — ScrapeGraphAI-shaped
  "describe what you want, the LLM extracts it", built strictly on a
  **user-provided** async callable (`async def my_llm(prompt: str) -> str`);
  no vendor LLM SDK, no new dependency (golden rule #5).
  `extract(source, schema=…, llm=…)` prompts with the result's markdown (or a
  raw string) + the schema — a JSON-Schema-style mapping or a plain
  natural-language field description — and returns the parsed JSON;
  `extract_into(result, …)` lands it in a frozen copy's `ScrapeResult.json`
  (+ `meta["llm_extracted"]=True`) — the same slot native structured engines
  (Firecrawl `/extract`, AnySite, Apify) fill, generalized to any engine's
  output. Lenient reply parsing (code fences / surrounding prose), a cheap
  dependency-free structural check (top-level `type`, `required` keys), a
  self-correcting retry loop that feeds the rejection reason back to the LLM
  (`max_retries`, default 1), content capped at `max_content_chars` (default
  150k chars), and `ExtractionError` carrying the last raw reply. Resolves
  TECH_DEBT #16.
- **Sync wrappers** — `scrape_sync(url, opts=None)` and
  `crawl_site_sync(url, opts=None, output=None, **kwargs)` for sync codebases.
  Each call runs the async API on a fresh event loop in a dedicated worker
  thread, so they keep working even when the calling thread has a running or
  *leaked* event loop (the Playwright-Sync-API failure mode where a bare
  `asyncio.run(scrape(...))` raises `RuntimeError`). `scrape_sync`
  deliberately takes no `pool` — engine-pool clients are bound to the loop
  they were created on; use `crawl_site_sync` (one loop spans the whole
  crawl) or the async API for connection reuse. Resolves TECH_DEBT #12.
- **Router budget & billing correctness** (P1 cluster, TECH_DEBT #1–#5):
  - `budget_mode` is now wired: a ladder step declaring
    `reset_user_fast_track` gets fresh `cost_usd`/`engines_tried` headroom
    (clock and reclassification guard kept); `reset_fresh_session`
    additionally resets `elapsed_ms`/`reclassifications`. No ladder sets a
    non-`inherit` mode yet, so default walks are unchanged (#1).
  - Race billing documented + pinned by tests: under the sequential race
    walk every invoked engine credits its cost to the walk budget — `sum_all`
    semantics matching real vendor spend (a blocked 200 was still billed);
    `winner_only`/`max` become distinct only with future concurrent fan-out
    (#2).
  - New `EngineCapabilities.bills_failed_attempts`, set on all 17
    per-call-billed paid engines, and
    `ladders.derive_budget_accounting(engines)` deriving a race's billing
    mode from those flags; a golden test asserts every `RaceStep` in
    `LADDERS` declares exactly the derived mode — billing modes are now
    data-checked, not trusted (#4).
  - Per-engine `avg_response_mb_estimate` set across the registry: browser
    session engines 15 MB, rendered-HTML proxy APIs 3 MB, markdown/JSON API
    engines 0.5 MB — feeding the router's per-engine cost gate, which reads
    each engine's own estimate (#3, #5).

### Changed

- Engine count 27 → 29 (pydoll, camoufox). README engine tables and
  `_VALID_ENGINES` ladder guard updated.
- The free stealth engines (`camoufox`, `pydoll`, `scrapling_stealth`) now read
  the unified `ScrapeOptions.proxy`, so a rotation pool can thread an exit IP
  into each.

## [0.4.0] - 2026-07-09

### Added

- **Serper engine** (`serper`) — page scrape via `https://scrape.serper.dev`, returning native text + markdown and any JSON-LD structured data. Pure httpx, no SDK. Reads `SERPER_API_KEY`.
- `maxun` engine — REST adapter for a self-hosted
  [Maxun](https://github.com/getmaxun/maxun) instance (open-source no-code
  web data extraction platform). Runs a recorded robot via the **verified**
  synchronous API contract (`x-api-key` auth,
  `POST /api/robots/{id}/runs`): the robot's captured list/field/crawl/search
  output lands in `ScrapeResult.json`, page markdown/HTML fill the text
  slots, screenshots and LLM summary/promptResult go to `meta`. The robot is
  selected via `extra["maxun_robot_id"]` (no URL→robot default);
  `extra["maxun_duplicate_for_url"]=True` opts into Maxun's documented
  robot-duplication flow to retarget the recorded robot at the scraped URL.
  Instance location via `MAXUN_BASE_URL` (default `http://localhost:8080`),
  key via `MAXUN_API_KEY`. Self-hosted → `cost_usd=0.0`.

### Changed

- Refreshed current docs/backlog status after the 0.3.0 social-engine release.

## [0.3.0] - 2026-06-28

### Added

- `telegram` engine + `telegram` / `vk` / `max` site classes. Telegram is a
  first-class **global** social platform (not lumped under `russian_social`):
  the free `telegram` engine fetches public-channel HTML previews
  (`t.me/s/<channel>`, single-message embeds) and parses them into normalized
  `Post` objects — text, media (photo/video + thumbnail), view count,
  timestamp, author — with no API key. `vk` (vk.com / vk.ru) and `max` (max.ru)
  get dedicated classes and stealth-led ladders; their structured path is the
  `apify_actor` engine — best-effort default Telegram/VK/Max actors added
  (flagged for verification) and wired into the vk/max ladders, plus
  host→platform labelling so `.social` is tagged correctly.
- Telegram-analytics engines `tgstat`, `telemetr`, `labelup` — structured REST
  adapters that normalize into `ScrapeResult.social` (`platform="telegram"`).
  `tgstat` follows TGStat's **verified** API contract (channel/posts/post
  routing, `token` query auth, `{"status":"ok","response":…}` envelope).
  `telemetr` follows Telemetr.io's **verified** API contract
  (https://api.telemetr.io/api-docs/openapi.json): `x-api-key` auth, and a
  two-step resolve — `/channels/search` maps a public `t.me` slug to Telemetr's
  numeric `internal_id`, then `/channel/info` (profile), `/messages/channel`
  (feed), or `/messages/by_id` (single post). `telemetr_internal_id` skips the
  search step; `telemetr_endpoint` forces a raw single-call path.
  `labelup` follows LabelUp's **verified** API contract
  (https://help.labelup.ru/article/23384): `GET /api/v2/accounts/statistics`
  with `Authorization: Bearer` + `X-Requested-With: XMLHttpRequest`. It is
  genuinely **multi-platform** (Instagram, YouTube, VK, Telegram, TikTok,
  RUTUBE, Dzen) — `scrape(url)` routes via the universal `?url=` param, with
  `network_id`/`nickname`/`uid`/`id` lookups and a `labelup_endpoint`
  raw-gateway escape hatch; the normalized `platform` is inferred from the URL
  host (or `extra["labelup_platform"]`), never hard-coded. `tgstat` /
  `telemetr` are raced into the `telegram` ladder behind the free engine.
- `platform_for_url()` helper in `scrapefold.social` — shared host→platform map
  (incl. Telegram/VK/Max/OK/Dzen/RuTube/Twitch/Threads/…) used by `apify_actor`
  and `labelup` to tag `.social` when the platform is known only from the URL.
- Normalized social entities (`scrapefold.social`) — a thin, best-effort layer
  that maps the many vendor JSON envelopes onto a stable
  `Profile` / `Post` / `Comment` shape (with a light `Author`), so callers read
  `post.like_count` without caring whether the vendor spelled it `likeCount`,
  `diggCount`, or `favorite_count`. `normalize_social(payload, platform=…,
  kind=…)` is hint-driven (engines derive `platform`/`kind` from the endpoint
  via `platform_kind`) and infers the kind from the field signature when no
  hint is given. `Post` carries a `media` list of `Media` (image/video URL +
  optional thumbnail), extracted best-effort from single-media posts, carousels
  / galleries (`media`, `images`, `childPosts`, …), nested media objects, and
  bare URL lists — de-duplicated, with the post permalink never mistaken for an
  image. The untouched vendor payload is always kept on each entity's
  `.raw`. `ScrapeResult` gains an additive `social` slot, populated by every
  social-structured engine — `scrapecreators`, `socialcrawl`, `apify_actor`,
  and `apify_linkedin` (LinkedIn profiles, with `firstName`/`lastName` composed
  into a single `name`). HTML/markdown engines (`anysite`, `firecrawl`, …) and
  the non-social-structured services (`exa` neural search, `outscraper`
  business insights) leave `social=None`. The types and `normalize_social` are
  re-exported from the package root.
- `apify_actor` engine — universal [Apify](https://apify.com) Actor adapter that
  generalises the LinkedIn-only `apify_linkedin` engine to any actor. Public
  social URLs (Instagram, TikTok, X/Twitter, YouTube, Facebook, Reddit,
  LinkedIn) route to a sensible default actor, while
  `extra["apify_actor_id"]` reaches any actor in Apify's catalogue and
  `apify_*` extras pass through into the actor `run_input`. Multi-item runs
  (post feeds, comment threads) return the full list in `ScrapeResult.json`;
  single-item runs return the object. Keyed by `APIFY_API_TOKEN`, lazy-imports
  the existing `scrapefold[apify]` SDK, and is registered under the `apify`
  alias. Wired into the Twitter/Instagram/TikTok/Facebook/YouTube/Reddit
  ladders.
- `tiktok` site class — TikTok URLs were previously unclassified and fell back
  to the general (plain-HTTP-first) ladder. They now classify to a dedicated
  `tiktok` class whose ladder leads with a paid social-gateway race
  (`scrapecreators`, `socialcrawl`, `apify_actor`) and falls back to a stealth
  unlocker, never plain `requests`.
- `pixelrag` engine — local [PixelRAG](https://github.com/StarTrail-org/PixelRAG)
  `pixelshot` adapter for URL-to-screenshot-tile capture plus an injected
  VLM/OCR tile reader hook (`extra["pixelrag_reader"]`) that turns the tiles
  into `ScrapeResult.text`, `ScrapeResult.markdown`, and structured per-tile
  JSON under `ScrapeResult.json["reader"]`. Without a reader, the engine still
  returns the capture manifest and file URIs. With `take_screenshot=True`, it
  also exposes the first tile in `ScrapeResult.screenshot_b64`. The optional
  `scrapefold[pixelrag]` extra is guarded for Python 3.12+ because PixelRAG's
  upstream package currently requires Python 3.12; Python 3.10/3.11 callers can
  set `PIXELRAG_BIN` to a `pixelshot` executable from another environment.
- `socialcrawl` engine — [SocialCrawl](https://www.socialcrawl.dev/) REST API
  gateway for public social data. Pure-`httpx`, JSON-native, and keyed by
  `SOCIALCRAWL_API_KEY`; normal URL scrapes map TikTok, Instagram, YouTube,
  Facebook, X/Twitter, LinkedIn, and Reddit URLs to matching SocialCrawl
  endpoints, while `extra["socialcrawl_endpoint"]` exposes arbitrary GET
  endpoints with prefixed query-param forwarding. Wired into the paid social
  and LinkedIn ladders.

### Changed

- Refreshed roadmap / technical-debt docs after the `0.2.0` release so
  router fan-out, MCP server implementation, sync wrappers, and remaining
  residential-proxy fallback work are tracked as future follow-ups.

## [0.2.0] - 2026-06-19

### Docs / Project

- **Project website** — published at **[scrapefold.com](https://scrapefold.com)**
  (GitHub Pages from `/docs`, custom domain with enforced HTTPS / Let's
  Encrypt). A single self-contained `docs/index.html` landing page (no build
  step): hero, value-prop cards, 15-engine grid, how-to-choose, quickstart,
  and an ecosystem footer.
- **README presentation layer** — hero block (logo + terminal-demo SVG),
  centered badges incl. a GitHub-stars badge, an at-a-glance stats row, a
  30-second taste snippet, and two star CTAs. The ASCII architecture diagram
  is replaced by `docs/assets/architecture.svg` (ASCII kept as a collapsible
  fallback). All existing technical tables are unchanged. (PR #2)
- **Brand assets** — hand-authored, self-contained SVGs under `docs/assets/`:
  `logo.svg` (dark-text, for light backgrounds), `logo-dark.svg` (white
  wordmark, for the dark landing hero), `demo.svg`, `architecture.svg`,
  `social-card.svg` (1200×630 OG card), and `favicon.svg`.
- **Ecosystem cross-links** — README "Built by" + landing footer now link
  Docfold, Datatera.ai, Orquesta AI, AI Agent Labs, and an author Connect
  line (LinkedIn / X / GitHub).
- `pyproject` `Homepage` → `https://scrapefold.com/`; repo topics set
  (`web-scraping`, `markdown`, `llm`, `anti-bot`, `crawler`, `playwright`,
  `mcp`, `python`, `firecrawl`, `stealth`).

### Added

- `exa` engine — Exa REST API integration for Search, Contents, Find Similar,
  Answer, and Agent runs. Pure-`httpx`, no SDK dependency; key from
  `EXA_API_KEY`. LinkedIn profile/company URLs default to Exa public
  `people` / `company` search modes and the engine is wired into the
  `linkedin_profile` and `linkedin_company` ladders.
- `scraperapi` engine — ScraperAPI REST adapter with JS render, country
  routing, premium proxies, forwarded headers/cookies, native markdown output,
  AI Parser JSON output, target status propagation, and dynamic credit-cost
  reporting from ScraperAPI response headers. Pure-`httpx`, no SDK dependency;
  key from `SCRAPERAPI_API_KEY`.
- `scrapecreators` engine — [Scrape Creators API](https://scrapecreators.com/)
  (`api.scrapecreators.com`) for public social-media data. Site-classified and
  JSON-native: maps the target URL to the matching `/v1/<platform>/<resource>`
  endpoint (TikTok, Instagram, YouTube, Twitter/X, Reddit) and returns the
  payload in `ScrapeResult.json` with post-converted text/markdown. `x-api-key`
  auth, pure-`httpx`, no extra dependency; key from `SCRAPECREATORS_API_KEY`.
  Force an endpoint via `extra["scrapecreators_endpoint"]`. Wired in as the
  lead option for the `twitter` / `instagram` / `reddit` ladders.
- `oxylabs` engine — Oxylabs Web Scraper API via the realtime (synchronous)
  endpoint `https://realtime.oxylabs.io/v1/queries`, using the `universal`
  source. Supports JS rendering (`render="html"`), screenshots
  (`render="png"`), geo routing (`country` → `geo_location`), and forwarded
  headers / cookies via the universal `context`. Credentials from
  `OXYLABS_USERNAME` / `OXYLABS_PASSWORD`; pure-`httpx`, no extra dependency.

## [0.1.1] - 2026-05-26

Patch release: bug fix for `crawl_site()` on anti-bot-protected sites.

### Fixed

- `crawl_site()` URL discovery (sitemap.xml / robots.txt / BFS link
  extraction) now escalates through the full engine ladder instead of
  using a hardcoded `httpx.AsyncClient`. Previously, on a
  Cloudflare-protected site the per-URL ladder worked but the upstream
  sitemap fetch returned the same 403 block page that the cheap
  `requests` engine would get → discovery collapsed to `{root}` and
  `crawl_site()` produced 1 page instead of N. (TECH_DEBT #10)

### Added

- `scrapefold.crawler.sitemap.FetchedDoc` and `DiscoveryFetcher` — public
  extension point for `discover_urls(..., fetcher=...)` so callers can
  inject custom fetch logic (e.g., to route through their own retry/proxy
  stack). When `fetcher=None`, the legacy httpx-based path is preserved
  for backward compatibility with existing direct callers.

### Filed (not yet implemented)

- `TECH_DEBT.md` #12 — `scrape_sync` / `crawl_site_sync` wrappers robust
  to leaked event loops in the caller's main thread (common with
  Playwright Sync API). Sync callers can work around with a 5-line
  `ThreadPoolExecutor` shim today; promoting that pattern into a public
  helper is queued for v0.2.

## [0.1.0] - 2026-05-26

First stable release. Promotes `0.1.0rc1` to stable with no code changes —
the RC bundle (simplify pass, detection fix, LLM-QA harness, generic
migration guide) is now the v0.1.0 surface.

### Added

- `docs/migration-guide.md` — generic four-pass migration guide for
  consumers replacing a hand-rolled fetcher cascade with scrapefold.
  Frontmatter + Problem / Proposed Solution / Affected Files / Test Plan
  / Edge Cases / Out of Scope structure.

### Frozen surface

- Public API: `from scrapefold import scrape, crawl_site, ScrapeOptions, ScrapeResult, CrawlResult, ScrapeEngine, EngineCapabilities, EngineError, RedirectScopeViolation, AllEnginesFailed`.
- CLI: `scrapefold scrape | crawl | list-engines | classify`, all with `--json` and `--per-page-dir` where applicable.
- MCP server: `scrapefold-mcp` entry point.
- Disk cache: sha256-keyed, mtime-TTL via `cache_ttl_days`, sharded directory layout.
- 19 engines registered (subset available by default; lazy import via extras).

### Tested

- 631 unit + integration tests passing offline.
- Live smoke on six real-world targets covering static HTML, SPA, Cloudflare-protected, and IP-geofenced architectures. 5/6 reachable; all reachable verified REAL (LLM QA score 7–9 / 10).

### Known limitations (tracked in `docs/TECH_DEBT.md`)

- Sitemap / robots / BFS discovery does not escalate engines (ticket #10, deferred to v0.2).
- No residential-proxy engine for IP-geofenced targets (ticket #11, deferred to v0.2).

## [0.1.0rc1] - 2026-05-25

First release candidate for v0.1.0. API surface is frozen pending downstream
consumer validation. No new functionality vs. 0.1.0a4 — this RC bundles the
simplify pass, the live-smoke detection fix, and the QA harness.

### Added

- `scripts/live_smoke.py --llm-qa` — opt-in flag that asks Claude
  (`claude-sonnet-4-6`, per the explicit-model audit rule) to classify each
  scraped page as REAL / BLOCK / THIN with a 0–10 confidence score. Verdicts
  surface in the markdown report. Scraped content is treated as untrusted
  data in the prompt.
- `docs/TECH_DEBT.md` ticket #10 — sitemap / robots / BFS discovery does not
  escalate engines (P2, deferred to v0.2). Surfaced by live smoke: a
  Cloudflare-protected homepage succeeds via `scrapling_stealth`, but the
  sitemap.xml fetch uses hard-coded httpx and returns a block page, so the
  crawl collapses to `{root}` and produces 1 page instead of N.
- `docs/TECH_DEBT.md` ticket #11 — no residential-proxy engine for
  IP-geofenced targets (P2, deferred to v0.2). A subset of targets are
  unreachable from US/EU IPs at the TCP layer; stealth doesn't help.
  Reinstate `brightdata_unlocker` as a real engine for geofenced site
  classes.

### Changed

- `scrapefold.detection.is_suspicious` — 403 / 429 / 503 status codes now
  flag suspicious regardless of body length. A live target was found
  returning a ~500-byte localised block page on 403 that was previously
  accepted as success because text > 200 chars; the router now correctly
  escalates to `scrapling_stealth` and serves real content (independently
  verified at 9/10 by the LLM QA pass). 404 / 401 / 410 remain non-suspicious
  (legitimate protocol responses).
- Simplify pass on Pack 5 + 6 — no functional change. Folded
  `path.exists`/`stat`/`read_text` into a single `_stat_and_read_sync` so
  `Cache.get_text` issues one `asyncio.to_thread` call instead of three.
  `EnginePool.aclose` now closes engines in parallel via `asyncio.gather`.
  `CrawlResult.failures` default switched from `field(default_factory=tuple)`
  to `= ()` (immutable, no factory needed).

### Validated

- Live smoke against six real-world targets covering static HTML, SPA,
  Cloudflare-protected, and IP-geofenced architectures. 5/6 reachable;
  all reachable targets verified REAL by an independent LLM. Cloudflare-
  protected targets escalate to `scrapling_stealth` and produce real
  homepage content (REAL 8–9/10).

## [0.1.0a4] - 2026-05-25

Pack 6 — Minimal Typer CLI with four subcommands (`scrape`, `crawl`, `list-engines`, `classify`), `--json` everywhere, and `--per-page-dir` for per-URL markdown output.

### Added — Pack 6 Typer CLI

- `src/scrapefold/cli.py` — full Typer CLI replacing the S1 stub. Four subcommands:
  - `scrape <url>` — single-URL scrape; `--json` emits `ScrapeResult` as JSON; `--output PATH` writes markdown to disk; `--engines <comma-list>` overrides engine selection.
  - `crawl <url>` — site crawl via `crawl_site()`; `--output PATH` writes stitched markdown; `--per-page-dir DIR` writes `<sha256(url)[:16]>.md` per crawled page with one `wrote <path> (<url>)` line to stderr per file; `--max-pages N` sets page limit; `--json` emits `{"output": path}` summary.
  - `list-engines` — prints sorted engine names; `--json` emits a JSON list.
  - `classify <url>` — prints the `SiteClass` the router assigns; `--json` emits `{"url": ..., "site_class": ...}`.
  - `--version` (root flag) — prints `__version__` and exits.
  - All errors fatal: `AllEnginesFailed` → exit 1; invalid options → exit 2 (Typer default).
- `tests/test_cli.py` — 16 tests via `typer.testing.CliRunner` covering all four subcommands, `--json` variants, `--output`, `--engines`, `AllEnginesFailed` exit code, `--version`, `--help` subcommand listing, and four `--per-page-dir` tests (file count, sha256 filename, stderr output, stitched + per-page coexistence).

### Added — `--per-page-dir` (per-URL markdown output)

The `crawl` subcommand's `--per-page-dir DIR` flag writes each `CrawlResult.pages[*].markdown` to `DIR/<sha256(url)[:16]>.md` and prints `wrote <path> (<url>)` to stderr. This is the load-bearing output path for downstream consumers that ingest one markdown file per URL.

### Changed — `__version__` bumped to `0.1.0a4`

## [0.1.0a3] - 2026-05-25

Pack 5 rescue — disk cache, EnginePool, and CrawlResult return type. All Pack 4 SSRF protections preserved. New modules are additive; existing engine/router/crawler SSRF contract is intact.

### Added — disk-backed TTL cache (`scrapefold.cache`)

- `src/scrapefold/cache.py` — SHA-256-keyed disk cache for `ScrapeResult`, TTL via file mtime, atomic writes, sharded directory layout, strict opts canonicalization. `Cache.get()` and `Cache.set()` accept `opts: ScrapeOptions` to honour `opts.skip_cache=True`. Disk I/O is non-blocking via `asyncio.to_thread`.
- `crawl_site()` / `crawl()` consult the cache when `opts.extra["cache_dir"]` is set; `opts.skip_cache=True` bypasses both read and write.

### Added — engine instance pool (`scrapefold.pool`)

- `src/scrapefold/pool.py` — `EnginePool` caches engine instances across the lifetime of a crawl, avoiding repeated TLS handshakes and SDK init costs. Idempotent `aclose()`, raises `RuntimeError` when accessed after close.
- `router.walk()` accepts an optional `pool: EnginePool | None` parameter. When `None` (default), an ephemeral pool is created and closed in a `finally` block. Caller-owned pools are never closed by the router.
- `crawl()` creates one `EnginePool` spanning all per-URL scrape calls and closes it in `finally`.

### Added — `CrawlResult` return type (`scrapefold.crawler.result`)

- `src/scrapefold/crawler/result.py` — `CrawlResult(pages, stitched_path, failures)` frozen dataclass. `crawl_site()` now returns `CrawlResult` instead of `Path`.
- `pages: tuple[ScrapeResult, ...]` — successfully scraped pages in discovery order.
- `stitched_path: Path | None` — where the stitched markdown was written.
- `failures: tuple[str, ...]` — per-URL failure strings formatted `"<url>:<ExcClass>:<msg>"`.
- `CrawlResult` and `EnginePool` re-exported from `scrapefold` top-level.

## [0.1.0a2] - 2026-05-24

Pack 3 — sequential router shell + Cloudflare Browser Rendering engine + comprehensive cost/policy/timeout enforcement. The public `scrape()` path is live for sequential ladders and explicit `opts.engines` overrides; the router now walks `RaceStep` members sequentially (parallel fan-out remains deferred to v0.2). `AllEnginesFailed` is now a structured exception with `.url` and `.failures` attributes. Release mechanics scaffolded: dynamic version from `__init__.py`, dep-freshness audit script, CHANGELOG gate.

### Changed — dependency floors refreshed

- Bumped lower-bound pins to current PyPI stable (pack-opening
  freshness policy from spec §4.4). Affected pins:
  - `tldextract` 5.0 → 5.3
  - `beautifulsoup4` 4.12 → 4.14
  - `typer` 0.12 → 0.25
  - `firecrawl-py` 4.0 → 4.27
  - `selenium` 4.25 → 4.44
  - `apify-client` 2.2 → 3.0
  - `mcp` 1.0 → 1.27
  - `pytest` 7.0 → 9.0
  - `pytest-asyncio` 0.21 → 1.3
  - `pytest-httpx` 0.30 → 0.36
  - `ruff` 0.4 → 0.15
  - `mypy` 1.8 → 2.1

### Changed — structured AllEnginesFailed (consumer error contract)

- `AllEnginesFailed` now carries `.url: str` and `.failures: list[str]`.
  Consumers no longer need to parse the exception message. The
  `failures` list shape is `"<engine>:<reason>:<detail>"` (e.g.
  `"firecrawl:error:404 Not Found"`, `"jina:empty"`,
  `"scrapingbee:unavailable"`, `"budget:cost"`). Downstream consumer
  migrations target this contract directly.

### Fixed — golden-rule violation: router now consults detection.is_suspicious

- The sequential router shell originally only checked `result.is_empty()`
  for advancement — a captcha page with non-empty text would have been
  returned as success. Per `docs/conventions/golden-rules.md` ("Suspicious-
  content detection lives in one place"), the router now calls
  `scrapefold.detection.is_suspicious(result)` after the empty check and
  advances on True with a `"<engine>:suspicious"` failure entry.
- Internal: `_resolve_policy` site_class arg typed as `SiteClass` instead
  of `str`; load-bearing `type: ignore[arg-type]` removed.

### Fixed — detection: is_suspicious short-text check is now conjoint

- `detection.is_suspicious` previously flagged any result with fewer than
  50 chars regardless of HTTP status. Now short-text is only suspicious
  when the status code is also non-2xx or when the result is empty,
  eliminating false positives for legitimately short pages (e.g. API
  endpoints returning `{"ok":true}`).

### Added — Pack 3 cloudflare engine

- `src/scrapefold/engines/cloudflare.py` — wraps Cloudflare Browser
  Rendering into scrapefold's `ScrapeEngine` ABC. Calls `/markdown` first
  (native markdown), falls back to `/content` (raw HTML → html_to_text).
  Env: `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`.
- `tests/test_engine_cloudflare.py` — 13 tests covering both endpoints,
  fallback paths, auth headers, body shape, is_available gating,
  registry registration.
- `docs/workflows/development.md` — env-var table fixed
  (`CLOUDFLARE_API_KEY` → `CLOUDFLARE_API_TOKEN`, matching Cloudflare's
  Bearer-token convention).

### Added — Pack 3 router shell (sequential)

- `src/scrapefold/router.py` — `async walk(url, opts) -> ScrapeResult` walks the per-site-class ladder. Honors `Policy` (paid_allowed / legal_constraints_blocked / geography_required), `WalkBudget` ceilings (`max_engines`, `max_cost_usd`, `timeout_s`), and the `engines_tried` dedup set. `RaceStep` entries are walked sequentially with a DEBUG log until Pack 9.
- `tests/test_router.py` — 13 tests covering happy path, empty-result advance, EngineError advance, unavailable-engine skip, AllEnginesFailed, unknown-engine skip, policy gating, budget halt, RaceStep skip, public `scrape()` delegation, failures-list, no-retry-within-walk, EngineError-non-propagation.
- `scrapefold.scrape(url, opts)` now delegates to `router.walk` instead of raising `NotImplementedError`.
- `tests/test_smoke.py` — the obsolete `NotImplementedError` smoke test is removed.
- Budget enforcement: 12 rounds of Codex review hardening. Key fixes: cost-budget skips engine (not halts walk), unavailable engines don't consume `max_engines` slot, timeout boundary uses `>=`, actual cost credits over estimate, `opts.engines` override respects all budget/policy gates, geography `()` means global.

### Added — release mechanics scaffolding

- `scripts/check-deps-fresh.sh` — pack-opening dep-floor nag: parses `pyproject.toml` lower bounds and warns when a dependency is more than 90 days behind PyPI stable.
- `scripts/check-changelog.sh` — PR-time gate: fails if `## [Unreleased]` is empty (no changes documented), scoped to the `[Unreleased]` section only.
- `scripts/check.sh` — version-equality gate added: reads dynamic version from `pyproject.toml` (via `tomli`) and asserts it matches `__init__.__version__`; prevents version drift between the two sources.
- `pyproject.toml` — `tomli` added as Python 3.10 compat dep for the version check; broken `obscura` and `brightdata` optional extras removed.

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

[Unreleased]: https://github.com/mihailorama/scrapefold/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/mihailorama/scrapefold/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mihailorama/scrapefold/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/mihailorama/scrapefold/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a4...v0.1.0
[0.1.0a4]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a3...v0.1.0a4
[0.1.0a3]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a2...v0.1.0a3
[0.1.0a2]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a0...v0.1.0a1
[0.1.0a0]: https://github.com/mihailorama/scrapefold/releases/tag/v0.1.0a0
