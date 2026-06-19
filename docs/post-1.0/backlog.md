---
purpose: "Engines + features intentionally deferred after the v0.2.0 release."
updated: "2026-06-19"
related:
  - ../TECH_DEBT.md
  - ../../CHANGELOG.md
---

# Post-v0.2 backlog

Tracked here so released lines stay focused and the deferrals remain
recoverable in future minor releases. For follow-up items that have a
specific code location and test plan, see [`TECH_DEBT.md`](../TECH_DEBT.md)
— this file is for broader scope items that don't fit the per-PR ticket
shape.

## Engines

### `obscura`

- **Why deferred:** No engine module exists yet; the pyproject extra was
  pointing to nothing importable, which created a broken-install cliff
  (`pip install scrapefold[obscura]` succeeded with no engine).
- **What it would do:** Free stealth browser, planned as an alternative
  to `cloakbrowser` in the `static_general` Step 3 race.
- **Future plan:** Land alongside the Bright Data-family fallback work.

### `brightdata` (Unlocker sync + async, Browser)

- **Why deferred:** Same as `obscura` — extra existed but no engine. Also
  `brightdata>=1.0` SDK is paid + auth-heavy; needs proper credential
  flow in tests.
- **What it would do:** Last-resort paid unlock at the end of nearly
  every difficulty ladder, plus the residential-proxy path for
  IP-geofenced targets (see `TECH_DEBT.md` #11).
- **Future plan:** Register as three names
  (`brightdata_unlocker_sync`, `brightdata_unlocker_async`,
  `brightdata_browser`) with user-facing alias `brightdata` →
  `brightdata_unlocker_sync`.

## Features

### MCP server implementation

- **Why deferred:** `scrapefold-mcp` exists as a console-script scaffold,
  but exits with a scaffold message. The actual S10 stdio tools/resources
  are not implemented yet.
- **Future plan:** Implement the contract in `docs/tools/agent-mode.md`
  (`scrape_url`, `crawl_site`, `list_engines`, `inspect_options`, and the
  read-only resources), then cover it with `tests/test_mcp_server.py`.

### ScraperAPI AI Parser lifecycle

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

- **Why deferred:** the current router still walks `RaceStep` members
  sequentially, and per-URL scrapes inside a crawl are serial.
- **Future plan:** Wire `asyncio.gather` with a bounded semaphore
  (`opts.max_concurrency`, default 4). Couples to the router-coupled
  P1 items in `TECH_DEBT.md` (#1 `budget_mode`, #2 race billing).

### `scrape_sync` / `crawl_site_sync` (sync wrappers)

- **Why deferred:** Async API is sufficient and sync callers can wrap
  with a 5-line `ThreadPoolExecutor` workaround (see `TECH_DEBT.md`
  #12 for the full context — found via consumer PR 1 smoke).
- **Future plan:** Land alongside the first sync-codebase consumer
  that needs more than ad-hoc usage. Implementation does worker-thread
  isolation internally so callers don't repeat the pattern.

### Residential-proxy escalation tier

- **Why deferred:** v0.2.0 ships the first residential-geo path via
  `oxylabs`, but the Bright Data-family fallback tier is still missing
  (see `TECH_DEBT.md` #11). Some geofenced targets are unreachable from
  US/EU IPs at the TCP layer; stealth doesn't help.
- **Future plan:** Add a new ladder tier above the unlocker engines
  for the `geofenced_*` site classes.

## Website / publication

The project site is live at **[scrapefold.com](https://scrapefold.com)**
(GitHub Pages from `/docs`, custom domain, enforced HTTPS). Follow-ups:

### PNG OG / social card

- **Issue:** `docs/assets/social-card.svg` is referenced by `og:image` /
  `twitter:image`. Several link-unfurlers (Slack, iMessage, some Twitter/X
  paths) do **not** render SVG OG images and will show no preview.
- **Plan:** Rasterize the card to a `social-card.png` (1200×630) and point
  the meta tags at the PNG; keep the SVG as the editable source. Needs a
  rasterizer (resvg / headless Chrome screenshot) — not available in the
  offline test env, so do it on a machine with a browser.
- **Affected:** `docs/index.html` (`og:image`, `twitter:image`).

### Web analytics

- **Issue:** No traffic analytics on the landing page, so we can't measure
  whether the publication push converts to installs / stars.
- **Plan:** Add a privacy-light, cookieless snippet (e.g. Plausible or a
  self-hosted equivalent) to `docs/index.html`. Confirm consent/footprint
  expectations before adding any third-party script.
