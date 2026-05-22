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

## Anti-bot escalation ladder

The recommended engine order is an explicit **escalation ladder**: start at the cheapest/fastest tier and only escalate when the previous tier failed *or returned suspicious content*. The router stops as soon as a tier returns a good response.

| Tier | Engines | Cost | Typical latency | When used |
|---|---|---|---|---|
| **T0** static | `requests` | 0 | <500 ms | Default first try for any URL |
| **T1** free JS | `scrapling`, `crawl4ai` | 0 | 3-5 s | T0 returned suspicious content (see below) or static fetch had no JS-rendered text |
| **T2** free stealth | `cloakbrowser`, `obscura` | 0 | 5-10 s | T1 hit anti-bot wall (Cloudflare challenge, captcha, 403) |
| **T3** paid fast | `firecrawl`, `scrapingbee`, `scrapingdog`, `cloudflare`, `jina` | ~$0.05-1 / 1k | 2-5 s | T2 still blocked, or site requires real residential IP for SSL/locale |
| **T4** paid full unlock | `brightdata_unlocker`, ScrapingBee `premium_proxy=True` | ~$1.5-3 / 1k | 10-30 s | T3 failed — site has aggressive bot detection (Akamai, PerimeterX, Datadome) |
| **T5** site-classified | `apify_linkedin`, `anysite` (LinkedIn/IG/Reddit), `scrapingdog` (LinkedIn/Amazon/Twitter) | varies | 5-15 s | URL pattern matches a vendor-specialized endpoint |

`selenium` ⚠️ deprecated — opt-in only via `engines=["selenium"]`, never auto-escalated.

### "Suspicious content" detection (T0 → T1 trigger)

The router treats a response as suspicious if any of:

- Text length < `min_text_chars` (default 200) after html_to_text
- Body contains anti-bot signature phrases: `"Just a moment..."`, `"Verify you are human"`, `"Checking your browser"`, `"Access denied"`, `"Please enable JavaScript"`, `"cf-browser-verification"`
- HTTP 403 / 503 with empty body
- `<noscript>` tag content dominates rendered body
- Body is mostly `<script>` tags (JS-rendered SPA with no SSR)

These heuristics live in `scrapefold/detection.py` (S2). Override per-call via `opts.extra["min_text_chars"]` and `opts.extra["antibot_phrases"]`.

### Stopping rules — preventing overkill

Escalation **stops** when any of:

| Rule | Default | Override |
|---|---|---|
| First tier returns a "good" response (not suspicious) | — | `opts.extra["accept_first_success"]=False` to keep trying |
| Total elapsed time exceeds `opts.timeout_s` | 60 s | per call |
| Cumulative cost would exceed `opts.extra["max_cost_usd"]` | 0.05 USD | per call |
| Engines tried reaches `opts.extra["max_engines"]` | 4 | per call |
| Explicit `opts.engines=[…]` was passed | — | router uses only that list, no auto-escalation |

This means a typical successful scrape of a friendly site costs **one `requests` call (~200 ms, $0)**. The full ladder runs only for hostile sites and stops at the first tier that works — preventing the "always run all 5 engines in parallel" overkill pattern.

### Parallel mode — only when explicitly requested

`opts.parallel=True` runs every engine in `opts.engines` concurrently and uses an LLM judge to pick / merge. This is **opt-in** and intended for offline quality benchmarking, not for routine scraping. The default `parallel=False` walks the ladder sequentially and stops early.

## Two console scripts

- `scrapefold` — Typer CLI (single-URL scrape, site crawl, introspection)
- `scrapefold-mcp` — MCP stdio server (4 tools, 2 resources) — for Claude Code / Cursor / agents

## See also

- [Golden rules](../conventions/golden-rules.md) — invariants
- [Testing](../workflows/testing.md) — marker strategy
- [Agent mode](../tools/agent-mode.md) — machine-parseable output
