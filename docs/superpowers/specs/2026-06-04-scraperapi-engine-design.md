---
title: "ScraperAPI engine + competitive-intel tracking"
date: "2026-06-04"
status: "approved"
related:
  - ../../../CONTRIBUTING.md
  - ../../conventions/golden-rules.md
  - ../../post-1.0/backlog.md
---

# ScraperAPI engine + competitive-intel tracking

## Why

ScraperAPI is a major SaaS scraping vendor conspicuously absent from the
scrapefold engine roster and comparison tables. Trigger: ScraperAPI moved
its **AI Parser** (LLM-based structured extraction) out of beta to GA on
2026-06-04, with new pricing (30,000 credits per parser create/edit, first
parser free, existing beta parsers grandfathered). This is both a
competitive data point worth tracking and the prompt to add ScraperAPI as a
first-class engine.

## Scope

In scope:

1. `scraperapi` scrape engine — standard scrape (HTML / JS render / country /
   premium proxy / native markdown) **plus** AI-Parser structured extraction
   that fills the `json` slot.
2. Offline test suite (TDD — tests written first).
3. Three doc artifacts: README comparison rows, backlog follow-up entry,
   and a new `docs/competitive-intel.md` change log.

Out of scope (deferred — see Backlog):

- AI Parser **lifecycle** management (creating / editing / deleting custom
  parsers — 30k credits each). The engine *uses* a parser by reference; it
  does not create one.
- Wiring `scraperapi` into the default escalation ladder. The engine is
  registered and selectable via `opts.engines=("scraperapi",)`, but the
  default T0→T4 order is unchanged in this cut (avoids reshuffling billing /
  step ordering).

## Engine design

File: `src/scrapefold/engines/scraperapi.py`. Pattern mirrors
`engines/scrapingdog.py` — pure `httpx`, no vendor SDK (golden rule #5).

Endpoint: `GET https://api.scraperapi.com/` with query params.

### `_adapt(opts, api_key, url) -> dict[str, str]`

Maps unified `ScrapeOptions` to ScraperAPI query params. Booleans serialize
as `"true"` / `"false"` strings.

| Unified option | ScraperAPI param | Notes |
|---|---|---|
| (always) | `api_key`, `url` | |
| `render_js` | `render=true\|false` | |
| `country` | `country_code` | |
| `premium_proxy` | `premium=true` | only when True |
| `wait_for_selector` | `wait_for_selector` | only when set |
| `output_format` (`markdown`/`auto`) | `output_format=markdown` | native markdown |
| `extra["scraperapi_autoparse"]` | `autoparse=true` | built-in domain parsers → JSON |
| `extra["scraperapi_*"]` | `*` | generic passthrough via `strip_extra_prefix` |

`language` and `user_agent` / `custom_headers` / `cookies` map to **request
headers** (via `build_target_headers`), not query params — same split as
scrapingdog.

### `_fetch` response handling

1. Issue the GET with adapted params + target headers.
2. Decide the native format from the response `content-type` and the
   requested params:
   - JSON response (AI Parser / `autoparse` / `output_format=json`) →
     `json` slot populated from `response.json()`; `text` / `markdown`
     best-effort (`json.dumps` pretty-print into `markdown` if no HTML).
   - Markdown response (`output_format=markdown`) → `markdown` slot set
     directly from `response.text`; `text` derived; `html=None`.
   - Otherwise HTML → `html_to_both(raw_html)` fills `text` + `markdown`,
     `html` = raw.
3. `meta["status_code"]` always set. Forward ScraperAPI's
   `sa-statuscode` / `sa-credit-cost` response headers into `meta`
   (`scraperapi_target_status`, `scraperapi_credit_cost`) when present.

### Capabilities

```python
NAME = "scraperapi"
CAPABILITIES = EngineCapabilities(
    js_rendering=True,
    stealth=False,
    screenshot=False,
    estimated_cost_usd=0.00049,   # 1 credit base; render≈10×, ultra-premium higher
    billing_unit="call",
    requires_api_key=True,
    proxy_type="datacenter",
    output_native_markdown=True,
    default_timeout_s=60,
)
SUPPORTED_OPTIONS = frozenset({
    "language", "country", "render_js", "wait_for_selector",
    "premium_proxy", "user_agent", "custom_headers", "cookies",
    "output_format", "timeout_s", "extra",
})
```

`wait_ms` is intentionally **not** supported — ScraperAPI exposes no
generic wait-milliseconds param (only `wait_for_selector`), so `wait_ms`
is stripped with a DEBUG log per golden rule #2 rather than silently
forwarded.

API key: ctor arg or `SCRAPERAPI_API_KEY` env. `is_available()` inherited
(False when no key). Network failure → `EngineError` via the base wrapper.

### Registration

- Lazy entry in `engines/__init__.py` `_REGISTRY` (`"scraperapi": lambda: …`).
- `scraperapi = []` extra in `pyproject.toml` (pure-httpx, no deps), added to
  the `all` aggregate extra.

## Test design

File: `tests/test_engine_scraperapi.py`. Offline, `pytest_httpx.HTTPXMock`,
TDD (written before the engine). Mirrors the 18-case scrapingdog suite, plus:

- `output_format=markdown` → `output_format=markdown` param AND markdown slot
  is set from the raw response (not re-derived from HTML).
- `extra["scraperapi_autoparse"]="true"` → `autoparse=true` param; a JSON
  response body lands in `result.json`.
- JSON `content-type` response → `json` slot populated, `text`/`markdown`
  best-effort non-empty.
- `_adapt` unit test: non-`scraperapi_`-prefixed extra keys are dropped.
- `meta` carries `scraperapi_credit_cost` when the `sa-credit-cost` header
  is present.

## Doc artifacts

1. **README.md** — add ScraperAPI to: the Engine Comparison table, the engine
   roster table (install: `pip install scrapefold[scraperapi]`), and the
   detailed `$/1k` comparison table (`scraperapi | SaaS | ✓ | … | datacenter |
   ✓ | ✓ | $0.49`). Note native markdown + AI Parser (`json`) in the
   "what it does" column.
2. **docs/post-1.0/backlog.md** — under `## Features`, add
   "ScraperAPI AI Parser lifecycle" (create/edit/manage custom parsers; 30k
   credits each; deferred because the engine only *references* a parser).
3. **docs/competitive-intel.md** — new dated change log of competitor
   pricing/feature shifts. Seed row: `2026-06-04 — ScraperAPI AI Parser GA`
   with the credit pricing and grandfathering note. Frontmatter + a table:
   `Date | Vendor | Change | Source | Impact on scrapefold`.

## Acceptance

- `pytest -m "not paid and not network"` green, including the new suite.
- `./scripts/check.sh` (lint + type-check + offline tests) passes.
- `scrapefold[scraperapi]` installs with no extra deps; engine importable and
  listed by the engine registry.
- All three doc artifacts present and internally consistent (links resolve).
