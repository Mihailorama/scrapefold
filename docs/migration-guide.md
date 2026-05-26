---
purpose: "Guide for migrating a multi-engine scraping cascade (Firecrawl + Scrapingdog + requests + Playwright + custom fallback) to scrapefold."
status: "STABLE"
audience: "Engineers replacing a hand-rolled fetcher cascade with scrapefold."
created: "2026-05-26"
related:
  - architecture/overview.md
  - conventions/golden-rules.md
  - tools/agent-mode.md
---

# Migrating to scrapefold from a multi-engine cascade

## Problem

Production scrapers rarely use one engine. A typical stack ends up looking
like this:

```
requests.get() → Obscura → CloakBrowser → Firecrawl → Scrapingdog → Apify → Anysite
       └──── 200–400 LOC of glue, retry logic, cache layer, fallback ordering ────┘
```

Each engine has its own auth, its own error shape, its own retry semantics,
and its own quirks (404 vs. empty body vs. captcha). Cache wrappers get
duplicated per engine. Adding or removing a fallback layer means editing N
files. And the moment a new anti-bot vendor appears, the cascade ossifies.

scrapefold replaces the entire glue layer with one call:

```python
from scrapefold import scrape

result = await scrape("https://example.com")
```

The router walks the engine ladder, applies content-quality detection
(`is_suspicious`), escalates on block pages, and returns a unified
`ScrapeResult` with `text`/`markdown`/`html`/`json` slots populated.

## Proposed Solution

Migrate in four passes, smallest-blast-radius first.

| Pass | What changes | Lines touched | Risk |
|---|---|---|---|
| 1 | Replace single-URL scrape calls | 20–80 per file | Low |
| 2 | Retire the fallback orchestrator | 200–400 in one file | Medium |
| 3 | Switch the disk cache | 50–100 | Low |
| 4 | Adopt `crawl_site()` for whole-site jobs | New code | Low |

Each pass is a separate commit; tests should pass between them.

### Pass 1 — single-URL scrape calls

The most common pattern in legacy scrapers is one function per vendor:

| Before | After |
|---|---|
| `firecrawl_app.scrape_url(url, params={...})` | `await scrape(url, opts=ScrapeOptions(render_js=True))` |
| `requests.get(url, headers=...).text` | `await scrape(url)` |
| `scrapingdog.get(url, params={...})` | `await scrape(url, opts=ScrapeOptions(stealth=True))` |
| `playwright.goto(url); page.content()` | `await scrape(url, opts=ScrapeOptions(render_js=True, stealth=True))` |
| `cloakbrowser.fetch(url)` | `await scrape(url, opts=ScrapeOptions(stealth=True, render_js=True))` |

Engines auto-route from `ScrapeOptions`. The same options work on every
engine; unsupported options are silently dropped with a DEBUG log (see
[golden-rules.md](conventions/golden-rules.md) §2).

### Pass 2 — retire the fallback orchestrator

If your repo has a `smart_fetcher.py` / `fetch_with_fallback.py` /
`cascade.py` that hand-rolls "try A, then B, then C, then D", delete it.
scrapefold's router does the cascade and stops at the first non-suspicious
response. Per-vendor classes (`FirecrawlFetcher`, `ScrapingdogFetcher`,
etc.) can be deleted in the same pass.

The router uses the `LADDERS` table (`src/scrapefold/ladders.py`) to pick
the engine order per site class. To pin the ladder for a specific call:

```python
opts = ScrapeOptions(engines=("requests", "scrapling_stealth", "firecrawl"))
```

### Pass 3 — switch the disk cache

scrapefold ships a sha256-keyed disk cache (`scrapefold.cache.Cache`) with
mtime-based TTL. If your repo already has a JSON-blob cache keyed on
`sha1(url)[:16]`:

```python
# Before
key = hashlib.sha1(url.encode()).hexdigest()[:16]
path = cache_dir / f"{key}.json"
if path.exists() and (time.time() - path.stat().st_mtime) < TTL:
    return json.loads(path.read_text())["html"]

# After
from scrapefold.cache import Cache, make_key
cache = Cache(cache_dir, ttl_days=30)
cached = await cache.get_text(make_key(url))
if cached is not None:
    return cached
```

Re-keying is a one-time cache miss; the cost is the next 30-day TTL
window. If your historic cache layout matters (audit, replay), keep the
old path read-only and let new entries write to the new cache.

`crawl_site()` integrates the cache by default — pass `cache_dir=...` to
the call and per-URL results are cached transparently.

### Pass 4 — adopt `crawl_site()` for whole-site jobs

If your repo has per-URL discovery (RSS, SERP, hand-curated partner-page
patterns, hardcoded URL lists), `scrape()` per URL is still the right
shape. But if you're discovering a whole site via its sitemap, switch:

```python
from scrapefold import crawl_site, ScrapeOptions

result = await crawl_site(
    "https://target.example.com/",
    opts=ScrapeOptions(max_pages=50, max_depth=3),
    output="site.md",            # stitched single-file markdown (optional)
    per_page_dir="pages/",       # one `<sha256(url)[:16]>.md` per page (optional)
    cache_dir="~/.cache/scrapefold",
    cache_ttl_days=7,
)
print(f"{len(result.pages)} pages, {len(result.failures)} failures")
```

`CrawlResult` exposes `.pages`, `.stitched_path`, and `.failures` — see
`src/scrapefold/crawler/result.py`.

## Affected Files

The four passes touch different file groups; tackle them in order.

### Per-call replacements (Pass 1)

| Legacy module | Replace with |
|---|---|
| `scrapers/firecrawl_fetcher.py` | direct `scrape()` calls |
| `scrapers/scrapingdog_fetcher.py` | direct `scrape()` calls |
| `scrapers/requests_fetcher.py` | direct `scrape()` calls |
| `scrapers/playwright_*.py` | direct `scrape()` calls with `render_js=True` |
| `scrapers/cloakbrowser_*.py` | direct `scrape()` calls with `stealth=True` |
| `scrapers/anysite_*.py` | direct `scrape()` calls |
| `scrapers/obscura_*.py` | direct `scrape()` calls with `stealth=True` |
| `scrapers/apify_*.py` | direct `scrape()` calls |

### Orchestrator deletion (Pass 2)

| Delete | Reason |
|---|---|
| `scrapers/smart_fetcher.py` (or equivalent cascade) | Router does this |
| Per-vendor fetcher classes | Router does this |

### Cache layer (Pass 3)

| Touch | Change |
|---|---|
| `scrapers/cache/http/*.py` | Drop in favor of `scrapefold.cache.Cache` |
| Callers of `_read_cache` / `_write_cache` | Update to `cache.get_text()` / `cache.put_text()` |

### Whole-site adoption (Pass 4)

| Touch | Change |
|---|---|
| Site-crawl entry points | Switch to `crawl_site()` |
| URL-discovery code (sitemap parsing) | Let `crawl_site()` handle it; pass `discovery_urls=...` for pre-discovered lists |

## Test Plan

### Unit / Functional Tests

- [ ] Parity test: pick 5–10 representative URLs from the legacy cache; assert `scrape(url)` returns markdown with text length ≥ 80% of the cached `html_to_text` length.
- [ ] Engine selection: assert `ScrapeOptions(render_js=True, stealth=True)` triggers `scrapling_stealth` (or higher) and not bare `requests`.
- [ ] Cache hit: scrape twice with `cache_dir=...`; assert second call is < 50 ms and second engine is `cache`.
- [ ] `AllEnginesFailed`: assert the structured `.url` and `.failures` fields are usable for retry logic.

### Integration / E2E Tests

- [ ] Run the legacy + scrapefold paths side-by-side on the production URL set; diff `markdown` length and presence of section headers. Expected drift ≤ 5%.
- [ ] Whole-site crawl on a 50-URL target. Assert `len(result.pages) >= 45` (some 404s allowed).
- [ ] Cloudflare/anti-bot target: assert the result's `meta["engine"]` is one of `scrapling_stealth` / `firecrawl` (not `requests`) and `len(markdown) > 500`.

### Test Commands

```bash
# Run scrapefold's offline gate
pytest -m "not paid and not network"

# Run consumer-side parity tests (uses cached URLs only, no network)
pytest tests/test_scrapefold_parity.py

# Optional: live smoke against real targets
python scripts/live_smoke.py --max-pages 5 --scrape-timeout 90
```

## Edge Cases

- **Sitemap-protected sites** — `crawl_site()`'s sitemap fetch currently uses
  a hardcoded httpx client (no engine escalation). On Cloudflare-protected
  sites, the homepage scrape succeeds via `scrapling_stealth` but the
  sitemap returns a block page and the crawl collapses to `{root}`. See
  [TECH_DEBT.md](TECH_DEBT.md) #10. Workaround: pre-discover URLs via the
  site's API/RSS/SERP and pass them as `discovery_urls=[...]`.
- **IP-geofenced targets** — some targets reject US/EU IPs at the TCP
  layer (timeout, not bot detection). No engine in the v0.1.0 ladder
  helps. See [TECH_DEBT.md](TECH_DEBT.md) #11 (residential-proxy engine
  deferred to v0.2). Workaround: run scrapefold from a geo-matched VPS.
- **`AllEnginesFailed` migration** — legacy callers may catch broad
  `Exception`. After migration, use the typed `AllEnginesFailed` with
  `.url` and `.failures` (a list of `"<engine>:<reason>:<detail>"` strings)
  so retry policy can be reason-aware.
- **Cache rehash** — switching from sha1 to sha256 is a one-time cache
  miss across the whole URL corpus. Schedule the migration during a
  low-traffic window or pre-warm by re-crawling top URLs.

## Out of Scope

- **Vendor-specific extras** that scrapefold deliberately doesn't model
  (e.g., Firecrawl's experimental "extract" endpoint, Apify's actor
  runs). Keep those as separate single-vendor scripts; they don't belong
  in a unified scraping cascade.
- **Browser automation** beyond fetch + render (clicks, scrolls, login
  flows). scrapefold's contract is "fetch this URL and give me content";
  for interactive flows, use Playwright directly.
- **Structured-extraction** (JSON schema enforcement, NER). scrapefold
  emits markdown; downstream parsers handle structure. Pair with
  [`docfold`](https://github.com/mihailorama/docfold) for document
  structuring after fetch.

## Related

- [docs/architecture/overview.md](architecture/overview.md) — module map and the escalation ladder
- [docs/conventions/golden-rules.md](conventions/golden-rules.md) — the five rules every engine adheres to
- [docs/tools/agent-mode.md](tools/agent-mode.md) — CLI + MCP for AI-agent consumers
- [docs/TECH_DEBT.md](TECH_DEBT.md) — known limitations callers should plan around
