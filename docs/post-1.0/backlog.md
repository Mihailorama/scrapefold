---
purpose: "Engines + features intentionally deferred after the v0.3.0 release."
updated: "2026-07-23"
related:
  - ../TECH_DEBT.md
  - ../../CHANGELOG.md
---

# Post-v0.3 backlog

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

- **Why deferred:** `oxylabs` ships the first residential-geo path, but the
  Bright Data-family fallback tier is still missing after v0.3.0
  (see `TECH_DEBT.md` #11). Some geofenced targets are unreachable from
  US/EU IPs at the TCP layer; stealth doesn't help.
- **Future plan:** Add a new ladder tier above the unlocker engines
  for the `geofenced_*` site classes.

### PixelRAG hosted/index search mode

- **Why deferred:** the shipped `pixelrag` engine handles URL capture plus an
  injected VLM/OCR reader for markdown / JSON. PixelRAG's hosted/index search
  API is query-to-index retrieval, not URL scraping, so it should not be hidden
  inside the default `scrape(url)` ladder.
- **Future plan:** add an explicit helper or CLI/MCP command for visual index
  search once there is a stable local contract for index URL, query shape, and
  result normalization.

### Social endpoint contract drift checks

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

## Distribution / agent-DX (adopted from the hound-mcp r/opencodeCLI thread, 2026-07)

Source: reddit.com/r/opencodeCLI/comments/1v1hfhk (458↑, 70 comments). Items
already shipped: idempotent agent setup prompt (`docs/install.md`),
`scrapefold install <client>`, `scrapefold doctor`, small-tool-count MCP
positioning, "engines not competitors" comparison section in README.
Deferred items below.

### `scrapefold update` self-update + update check

- **Why:** hound ships `-v` (version + update status), `-u` (self-update),
  `--reinstall`. Keeps installs current without users tracking PyPI.
- **What:** `scrapefold doctor` gains a PyPI latest-version check (network,
  opt-in); `scrapefold update` runs `pip install -U scrapefold[...]`
  preserving installed extras.

### Error honesty in `ScrapeResult`

- **Why:** hound v10.4.0's headline fix — 4xx/5xx error pages used to flow
  through as "content" and agents mistook error-page HTML for data. Their
  fix: flag it and replace content with the error message. scrapefold's
  `is_suspicious` already gates escalation, but the *final* result of an
  exhausted ladder can still be an error page presented as content.
- **What:** a `ScrapeResult.error`/`degraded` slot set when the best
  available response still looks like a block/error page, so MCP/CLI
  consumers can tell "real content" from "best effort".

### Wayback Machine fallback engine

- **Why:** hound auto-recovers dead links (404/451/500) from the Internet
  Archive and honestly marks `source=archive.org` + snapshot date. Loved in
  the thread.
- **What:** a free `wayback` engine at the end of ladders for 404-class
  failures, filling `ScrapeResult.json` with `{"source": "archive.org",
  "snapshot_date": ...}`.

### Focused extraction parameter (`focus=`)

- **Why:** the deepest thread subdiscussion: agents waste context fetching
  whole pages/PDFs. hound's `focus="query"` BM25-filters to relevant blocks;
  claimed >5-10% context savings; "if the agent doesn't discover the
  feature, the feature might as well not exist" — must be prominent in the
  MCP tool description.
- **What:** `scrape_url(..., focus="query")` in the MCP server + `--focus`
  CLI flag, BM25 over markdown blocks (stdlib-only, no new deps).

### Token-cost transparency for MCP tool definitions

- **Why:** hound advertises "2,746 tokens for all 6 tools + instructions".
  A concrete, checkable number that lands well with agent users.
- **What:** measure scrapefold's 4 tool defs + instructions, pin with a test
  budget (e.g. < 1,500 tokens), publish the number in README/landing.

### MCP web-tools benchmark

- **Why:** OP + commenter agreed a standardized eval (fetch accuracy, search
  recall, crawl completeness, anti-bot success, token efficiency) is an
  ecosystem gap. scrapefold already has per-engine ratings; a public bench
  would be strong distribution content.
- **What:** ties into the existing evaluation ideas in ragfold; could reuse
  its metrics runner against a fixed URL corpus.
