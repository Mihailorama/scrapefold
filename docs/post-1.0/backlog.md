---
purpose: "Engines + features intentionally deferred after the v0.3.0 release."
updated: "2026-11-01"
related:
  - ../TECH_DEBT.md
  - ../../CHANGELOG.md
---

# Post-v0.3 backlog

> Since 2026-11-01 the Plane project **SCRAPEFOLD** (tasks.aiagentlbs.com, workspace `sg`)
> is the source of truth for scrapefold tasks. Entries here mirror their Plane issue.

Tracked here so released lines stay focused and the deferrals remain
recoverable in future minor releases. For follow-up items that have a
specific code location and test plan, see [`TECH_DEBT.md`](../TECH_DEBT.md)
— this file is for broader scope items that don't fit the per-PR ticket
shape.

## Engines

## Features

### ScraperAPI AI Parser lifecycle

- **Plane:** SCRAPEFOLD-13

- **Why deferred:** The `scraperapi` engine *uses* a parser by reference
  (`extra["scraperapi_autoparse"]` for built-in domains, or a custom
  parser id). It does not create/edit/delete parsers. ScraperAPI's AI
  Parser went GA 2026-06-04 charging 30,000 credits per parser create
  **and** per edit (first parser free) — a costly, stateful operation
  that doesn't fit the stateless per-call engine model.
- **Future plan:** A separate management helper (not a scrape engine) that
  wraps the AI Parser CRUD API, with credit-cost guards before any
  create/edit call. Tracked in `docs/competitive-intel.md`.

### `crawl_site` parallel fan-out

- **Plane:** SCRAPEFOLD-6

- **Why deferred:** the current router still walks `RaceStep` members
  sequentially, and per-URL scrapes inside a crawl are serial.
- **Future plan:** Wire `asyncio.gather` with a bounded semaphore
  (`opts.max_concurrency`, default 4). Couples to the router-coupled
  P1 items in `TECH_DEBT.md` (#1 `budget_mode`, #2 race billing).

### Residential-proxy escalation tier

- **Plane:** SCRAPEFOLD-19

- **Why deferred:** `oxylabs` ships the first residential-geo path, but the
  Bright Data-family fallback tier is still missing after v0.3.0
  (see `TECH_DEBT.md` #11). Some geofenced targets are unreachable from
  US/EU IPs at the TCP layer; stealth doesn't help.
- **Future plan:** Add a new ladder tier above the unlocker engines
  for the `geofenced_*` site classes.

### PixelRAG hosted/index search mode

- **Plane:** SCRAPEFOLD-14

- **Why deferred:** the shipped `pixelrag` engine handles URL capture plus an
  injected VLM/OCR reader for markdown / JSON. PixelRAG's hosted/index search
  API is query-to-index retrieval, not URL scraping, so it should not be hidden
  inside the default `scrape(url)` ladder.
- **Future plan:** add an explicit helper or CLI/MCP command for visual index
  search once there is a stable local contract for index URL, query shape, and
  result normalization.

### Social endpoint contract drift checks

- **Plane:** SCRAPEFOLD-15

- **Why deferred:** v0.3.0 now depends on several third-party social APIs
  (`socialcrawl`, `tgstat`, `telemetr`, `labelup`, Apify actors). Most are
  pure REST adapters with offline tests pinned to observed contracts; vendor
  docs and actor defaults can still drift.
- **Future plan:** Add a small opt-in live contract check script for keyed
  social engines, marked `network`/`paid`, that verifies auth shape and one
  low-cost endpoint per provider before release branches.

## Website / publication

The project site is live at **[scrapefold.com](https://scrapefold.com)**
(GitHub Pages from `/docs`, custom domain, enforced HTTPS). Follow-ups:

### Web analytics

- **Plane:** SCRAPEFOLD-16

- **Issue:** No traffic analytics on the landing page, so we can't measure
  whether the publication push converts to installs / stars.
- **Plan:** Add a privacy-light, cookieless snippet (e.g. Plausible or a
  self-hosted equivalent) to `docs/index.html`. Confirm consent/footprint
  expectations before adding any third-party script.

## Distribution / agent-DX (adopted from the hound-mcp r/opencodeCLI thread, 2026-07)

Source: reddit.com/r/opencodeCLI/comments/1v1hfhk (458↑, 70 comments). Items
already shipped: idempotent agent setup prompt (`docs/install.md`),
`scrapefold install <client>`, `scrapefold doctor`, small-tool-count MCP
positioning, "engines not competitors" comparison section in README, the
`wayback` fallback engine, `focus=` BM25 extraction (CLI `--focus` +
MCP `scrape_url(focus=...)`), `scrapefold update` self-update, structured
MCP failures (`{"error", "failures"}` instead of raw exceptions), and a
pinned token budget for MCP tool definitions (~750-token estimate,
`test_tool_definitions_within_token_budget`). Deferred items below.

### MCP web-tools benchmark

- **Plane:** SCRAPEFOLD-17

- **Why:** OP + commenter agreed a standardized eval (fetch accuracy, search
  recall, crawl completeness, anti-bot success, token efficiency) is an
  ecosystem gap. scrapefold already has per-engine ratings; a public bench
  would be strong distribution content.
- **What:** ties into the existing evaluation ideas in ragfold; could reuse
  its metrics runner against a fixed URL corpus.
