---
purpose: "High-level architecture — package layout, engine ABC, router, crawler, public API."
updated: "2026-05-22"
related:
  - ../conventions/golden-rules.md
  - ../workflows/testing.md
  - ../../CONTRIBUTING.md
---

# Architecture

## Package layout

```
src/scrapefold/
├── __init__.py          public re-exports: scrape, crawl_site, ScrapeOptions, ScrapeResult, ScrapeEngine
├── options.py           ScrapeOptions dataclass — single unified parameter schema
├── result.py            ScrapeResult dataclass
├── engines/
│   ├── base.py          ScrapeEngine ABC + EngineCapabilities + EngineError
│   ├── __init__.py      lazy engine registry
│   └── <name>.py        one file per engine (16 planned)
├── router.py            (S7) auto-select + sequential fallback chain
├── parallel.py          (S7) run-many + LLM-judge with injected callable
├── html_to_text.py      (S2) shared HTML → text / markdown
├── url_utils.py         (S2) scan_urls, classify_url, is_technical_url, formaturl
├── crawler/             (S8) sitemap → BFS → filters → stitcher
├── cache.py             (S8) disk-backed TTL cache
├── vision.py            (S6/S7) optional analyze_screenshot_with_llm(callable_llm)
├── cli.py               (S9) Typer entry — `scrapefold` console script
└── mcp_server.py        (S10) MCP stdio server — `scrapefold-mcp` console script
```

## Data flow — single URL

```
scrape(url, opts)
  └─ ScrapeRouter.pick_chain(url, opts.engines)
       └─ for engine in chain:
            ├─ engine._strip_unsupported(opts)        # drop opts engine doesn't know
            ├─ engine._fetch(url, stripped_opts)      # vendor-specific
            └─ on success → ScrapeResult              # uniform return
            └─ on failure → EngineError, try next
```

## Data flow — whole site

```
crawl_site(url, opts)
  ├─ crawler.sitemap.parse(url)                 # /sitemap.xml → /robots.txt → BFS fallback
  ├─ crawler.filters.apply(urls)                # drop mailto/utm/login/share/non-html
  ├─ for each url:
  │   └─ cache.get_or_fetch(url, opts)          # sha256(url)+sha256(opts) key, TTL
  │        └─ scrape(url, opts)
  └─ crawler.stitcher.write(results, output)    # one big markdown + YAML front-matter per page
```

## Engine ABC contract

Every engine subclasses `ScrapeEngine` and declares:

- `NAME: ClassVar[str]` — the registry key
- `CAPABILITIES: ClassVar[EngineCapabilities]` — what it can do
- `SUPPORTED_OPTIONS: ClassVar[frozenset[str]]` — names of `ScrapeOptions` fields it honors
- `async def _fetch(self, url, opts) -> ScrapeResult` — the vendor call

The base class handles:
- Stripping unsupported options (with DEBUG log)
- Wall-clock timing → `result.elapsed_ms`
- Uniform error wrapping → `EngineError`
- `is_available()` default — checks api_key when `requires_api_key=True`

## Unified options

A single `ScrapeOptions` dataclass passed to every engine. Examples:

```python
ScrapeOptions(language="ru", country="ru", render_js=True, stealth=True, wait_ms=8000)
```

Engines silently drop fields they don't honor — same call site works for Scrapingdog (TLD-routed proxy), Firecrawl (Accept-Language header), ScrapingBee (`country_code`), Jina (no-op).

Full adapter matrix: see `CONTRIBUTING.md` § "Add an engine" once filled in by each engine PR.

## Result formats — text / markdown / html / json

`ScrapeResult` carries four format slots, populated independently:

| Field | Always set? | Source |
|---|---|---|
| `text` | ✅ yes | Always, post-converted from whichever native form |
| `markdown` | ✅ yes | Always, engine-native or post-converted from HTML |
| `html` | optional | Only when an engine returned HTML (most browser engines) |
| `json` | optional | Only when an engine returned **structured data natively** (Firecrawl `/extract`, AnySite endpoints, Apify actors, vendor schema-extract endpoints) |

`ScrapeOptions.output_format` is a **hint** to the engine about which native form is cheapest to produce — `auto` lets the engine choose. The hint does not gate which slots are filled: an HTML-returning engine still produces `text` and `markdown` via post-conversion regardless of the hint.

```python
res = await scrape(url, opts=ScrapeOptions(output_format="markdown"))
res.text       # always
res.markdown   # always
res.html       # may be None
res.json       # None unless the engine extracts structured data

# Uniform access
res.get_format("markdown")
res.get_format("json")  # raises ValueError if not "text|markdown|html|json"
```

For *structured extraction* (passing a JSON schema and getting native JSON back), engines that support it accept an `extra={"schema": {...}}` key. Engines that don't support structured extraction fill `json=None` and the call falls back to text/markdown/html.

## Anti-bot escalation — per-site-class ladders

The router does not walk a universal T0-T5 chain. Each `SiteClass` (30 classes: LinkedIn family, Amazon, social, SERP, Cloudflare/Datadome/Akamai/PerimeterX, paywall, government, `static_general`, …) has its own ordered **ladder** of `SequentialStep` and `RaceStep` entries. The mapping lives in `src/scrapefold/ladders.py`; the router (S7) consumes it.

### Data shape

```python
# src/scrapefold/ladders.py
SiteClass = Literal["linkedin_profile", "amazon_product", "cloudflare_protected", ...]

@dataclass(frozen=True)
class SequentialStep(_StepBase):
    engine: str

@dataclass(frozen=True)
class RaceStep(_StepBase):
    engines: tuple[str, ...]
    winner_policy: Literal["first_non_suspicious", "first_complete", "highest_text_length"]
    cancel_policy: Literal["cancel_immediately", "cancel_with_grace", "let_finish"]
    cancel_grace_ms: int
    budget_accounting: Literal["winner_only", "sum_all", "max"]

LadderStep = Union[SequentialStep, RaceStep]
LADDERS: dict[SiteClass, tuple[LadderStep, ...]]
```

Race semantics are encoded as data on the step, not as router convention.

### URL classification

`classify_url(url)` walks an ordered `URL_PATTERNS` table (specific-first) and returns a `SiteClass`. The fallback for unknown URLs is `"static_general"` which uses the general ladder. Response-content reclassification (Cloudflare challenge page, Datadome cookie, …) is driven by `SIGNATURES` consumed in `detection.py` (S2). A 22-row `GOLDEN_CORPUS` constant pins URL→class behavior; the parametrized test `test_url_classification_golden_corpus` makes any regex reorder regression visible.

### General ladder (for `static_general`)

| Step | Engines | Cost | Notes |
|---|---|---|---|
| 1 | `requests` (SequentialStep) | $0 | Cheapest first |
| 2 | `scrapling_stealth`, `crawl4ai` (RaceStep, winner_only) | $0 | Free JS rendering |
| 3 | `cloakbrowser`, `obscura` (RaceStep) | $0 | Free stealth browsers |
| 4 | `firecrawl`, `scrapingbee`, `scrapingdog`, `cloudflare`, `jina` (RaceStep, **sum_all**) | $0.50-1.50 / 1k | Paid fan-out; every attempt billed |
| 5 | `brightdata_unlocker_sync` | $1.50 / 1k | Last-resort unlock |

### LinkedIn (specialized — never starts with `requests`)

LinkedIn ladders skip plain HTTP and lead with a paid race over vendor-specialized endpoints. Example for `linkedin_profile`:

```python
(
    RaceStep(engines=("apify_linkedin", "anysite", "scrapingdog", "exa"),
             budget_accounting="sum_all"),
    SequentialStep(engine="brightdata_unlocker_sync", cost=0.0015),
)
```

`test_linkedin_never_starts_with_requests` enforces the no-plain-HTTP rule across all five LinkedIn classes.
Exa uses public people/company search defaults for LinkedIn profile/company URLs; Sales Navigator remains outside the automatic Exa path.

### Difficulty classes

`cloudflare_protected` / `datadome_protected` / `akamai_protected` / `perimeterx_protected` are reached via response-signature reclassification (counter `WalkBudget.reclassifications`, capped at 3). They lead with stealth-browser races (`cloakbrowser`, `obscura`, `scrapling_stealth`) — never plain `requests`.

### Multi-mode engines = distinct registry names

Bright Data Unlocker has two modes; Scrapling has two modes. Rather than carry a mode toggle on each call, the registry treats them as distinct engines:

- `brightdata_unlocker_sync` / `brightdata_unlocker_async`
- `scrapling_stealth` / `scrapling_fast`

User-facing aliases live in `ENGINE_ALIASES` (e.g. `opts.engines=["scrapling"]` resolves to `scrapling_stealth`). The benefit: `WalkBudget.engines_tried` is a set of unambiguous names — no engine instance gets retried in a different mode without an explicit alias.

### Walk-time contracts (pure functions)

The router consumes three pure functions from `ladders.py`:

```python
is_step_allowed(step, policy) -> tuple[bool, str | None]
check_budget(step, walk, *, timeout_s, max_engines, max_cost_usd) -> None
                                                                # raises BudgetExceeded
_estimate_step_cost(step, avg_response_mb=2.0) -> float
```

`is_step_allowed` enforces `Policy(paid_allowed, legal_constraints_blocked, geography_required)`. `check_budget` accounts for **race fan-out** in the engine-count ceiling so a 3-engine race cannot start with only 1 engine slot remaining. `_estimate_step_cost` converts `(estimated_cost_usd, billing_unit)` into a per-call USD figure — `gb` billing scales with `avg_response_mb`.

### Default policies

`DEFAULT_POLICY: dict[SiteClass, Policy]` holds class-level overrides. `government` ships with `paid_allowed=False` so commercial scraping APIs are skipped unless the caller explicitly opts in.

### "Suspicious content" detection

`scrapefold/detection.py` (S2) owns the heuristics (text length < `min_text_chars`, anti-bot phrases, `<noscript>`-dominant body, mostly-`<script>` body). The router calls `detection.is_suspicious(result)` and, on `True`, advances to the next step or reclassifies via `SIGNATURES`.

### Stopping rules — preventing overkill

The walk stops when any of:

| Rule | Default ceiling | Override |
|---|---|---|
| Step returns a non-suspicious response | — | `opts.extra["accept_first_success"]=False` to keep trying |
| `walk.elapsed_ms / 1000 >= opts.timeout_s` | 60 s | `opts.timeout_s=N` |
| `walk.cost_usd + step_cost > max_cost_usd` | 0.05 USD | `opts.extra["max_cost_usd"]=N` |
| `len(walk.engines_tried) + step_fanout > max_engines` | 4 | `opts.extra["max_engines"]=N` |
| `walk.reclassifications >= MAX_RECLASSIFICATIONS` | 3 | (class invariant) |
| Explicit `opts.engines=[…]` was passed | — | router uses only that list |

A typical scrape of a friendly site costs one `requests` call (~200 ms, $0). The full ladder runs only for hostile sites.

### Parallel mode — only when explicitly requested

`opts.parallel=True` runs every engine in `opts.engines` concurrently and uses an LLM judge to pick / merge. This is **opt-in** and intended for offline quality benchmarking, not for routine scraping. The default `parallel=False` walks the ladder sequentially and stops early.

## Two console scripts

- `scrapefold` — Typer CLI (single-URL scrape, site crawl, introspection)
- `scrapefold-mcp` — MCP stdio server (4 tools, 2 resources) — for Claude Code / Cursor / agents

## See also

- [Golden rules](../conventions/golden-rules.md) — invariants
- [Testing](../workflows/testing.md) — marker strategy
- [Agent mode](../tools/agent-mode.md) — machine-parseable output
