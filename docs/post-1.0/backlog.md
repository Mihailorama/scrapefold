---
purpose: "Engines + features intentionally deferred from v0.1.0."
updated: "2026-05-23"
related:
  - ../superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md
---

# Post-v0.1.0 backlog

Tracked here so the v0.1.0 surface stays focused and the deferrals are
recoverable in v0.2.0+.

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
  every difficulty ladder.
- **v0.2.0 plan:** Wave 3 Pack 3A. Register as three names
  (`brightdata_unlocker_sync`, `brightdata_unlocker_async`,
  `brightdata_browser`) with user-facing alias `brightdata` →
  `brightdata_unlocker_sync`.

## Features

(populated by later packs)
