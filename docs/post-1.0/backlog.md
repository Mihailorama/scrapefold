---
purpose: "Engines + features intentionally deferred from v0.1.x."
updated: "2026-05-26"
related:
  - ../TECH_DEBT.md
  - ../../CHANGELOG.md
---

# Post-v0.1.x backlog

Tracked here so the v0.1.x line stays focused and the deferrals are
recoverable in v0.2.0+. For follow-up items that have a specific code
location and test plan, see [`TECH_DEBT.md`](../TECH_DEBT.md) — this
file is for broader scope items that don't fit the per-PR ticket shape.

## Engines

### `obscura`

- **Why deferred:** No engine module exists yet; the pyproject extra was
  pointing to nothing importable, which created a broken-install cliff
  (`pip install scrapefold[obscura]` succeeded with no engine).
- **What it would do:** Free stealth browser, planned as an alternative
  to `cloakbrowser` in the `static_general` Step 3 race.
- **v0.2.0 plan:** Land as Wave 3 Pack 3A alongside `brightdata`.

### `brightdata` (Unlocker sync + async, Browser)

- **Why deferred:** Same as `obscura` — extra existed but no engine. Also
  `brightdata>=1.0` SDK is paid + auth-heavy; needs proper credential
  flow in tests.
- **What it would do:** Last-resort paid unlock at the end of nearly
  every difficulty ladder, plus the residential-proxy path for
  IP-geofenced targets (see `TECH_DEBT.md` #11).
- **v0.2.0 plan:** Wave 3 Pack 3A. Register as three names
  (`brightdata_unlocker_sync`, `brightdata_unlocker_async`,
  `brightdata_browser`) with user-facing alias `brightdata` →
  `brightdata_unlocker_sync`.

## Features

### `crawl_site` parallel fan-out

- **Why deferred:** v0.1.x ships sequential-only (`RaceStep` skipped
  with DEBUG log per the release split documented in `TECH_DEBT.md`).
  Per-URL scrapes inside a crawl are serial.
- **v0.2.0 plan:** Wire `asyncio.gather` with a bounded semaphore
  (`opts.max_concurrency`, default 4). Couples to the router-coupled
  P1 items in `TECH_DEBT.md` (#1 `budget_mode`, #2 race billing).

### `scrape_sync` / `crawl_site_sync` (sync wrappers)

- **Why deferred:** Async API is sufficient and sync callers can wrap
  with a 5-line `ThreadPoolExecutor` workaround (see `TECH_DEBT.md`
  #12 for the full context — found via consumer PR 1 smoke).
- **v0.2.0 plan:** Land alongside the first sync-codebase consumer
  that needs more than ad-hoc usage. Implementation does worker-thread
  isolation internally so callers don't repeat the pattern.

### Residential-proxy escalation tier

- **Why deferred:** No residential-proxy engine in v0.1.x (see
  `TECH_DEBT.md` #11). Some geofenced targets are unreachable from
  US/EU IPs at the TCP layer; stealth doesn't help.
- **v0.2.0 plan:** Adds a new ladder tier above the unlocker engines
  for the `geofenced_*` site classes.
