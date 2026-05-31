---
purpose: "Documentation index — start here. Navigate to architecture, workflows, and conventions."
updated: "2026-05-27"
related:
  - architecture/overview.md
  - workflows/development.md
  - conventions/golden-rules.md
  - ../CHANGELOG.md
---

# scrapefold docs

Unified Python library for web scraping — single URL or whole-site → markdown, with stealth, JS rendering, and LLM-ready output. Wraps 14 vendor APIs + local stealth browsers + a baseline `requests` engine behind one async interface.

## Where to look

| If you want to… | Start at |
|---|---|
| Understand the architecture | [architecture/overview.md](architecture/overview.md) |
| Migrate an existing scraper to scrapefold | [migration-guide.md](migration-guide.md) |
| Set up a dev environment | [workflows/development.md](workflows/development.md) |
| Run tests | [workflows/testing.md](workflows/testing.md) |
| Know what rules contributors must follow | [conventions/golden-rules.md](conventions/golden-rules.md) |
| Understand agent mode (CLI / MCP, machine-friendly output) | [tools/agent-mode.md](tools/agent-mode.md) |
| Add a new engine | [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| See deferred work and known limitations | [TECH_DEBT.md](TECH_DEBT.md), [post-1.0/backlog.md](post-1.0/backlog.md) |

## Project status

**v0.1.1 — shipped to PyPI on 2026-05-26.** `pip install scrapefold`.

| PR / Pack | Engines / features | Version |
|---|---|---|
| S1 ✅ | Scaffold: pyproject, base ABC, options/result, CLI/MCP stubs, docs | 0.1.0a0 |
| Pack 2A-C ✅ | `requests`, Jina, Cloudflare, Crawl4AI, Firecrawl, ScrapingBee, Scrapingdog, Selenium, Scrapling (fast + stealth), Apify-LinkedIn, Outscraper, AnySite, Cloakbrowser | 0.1.0a1 |
| Pack 3 ✅ | Router + ladders + parallel + detection | 0.1.0a2 |
| Pack 4 ✅ | html_to_text + vision | 0.1.0a3 |
| Pack 5 ✅ | Crawler (sitemap → BFS → stitch → cache) + `CrawlResult` + `EnginePool` | 0.1.0a4 |
| Pack 6 ✅ | CLI (Typer): `scrape`, `crawl`, `list-engines`, `classify` + `--per-page-dir` | 0.1.0a5 |
| Pack 7 ✅ | RC tag + consumer migration guide (`docs/migration-guide.md`) | 0.1.0rc1 |
| Pack 8 ✅ | First stable release + PyPI publish via trusted publishing | 0.1.0 |
| Patch ✅ | TECH_DEBT #10 — discovery escalates through engine ladder; sync-wrapper backlog (#12) filed | 0.1.1 |
| PR #1 ✅ | `oxylabs` engine (Web Scraper API realtime, residential geo, render/screenshot, pure-httpx) | Unreleased |
| PR #2 ✅ | Presentation layer: README hero + brand SVGs + landing site; **[scrapefold.com](https://scrapefold.com)** live (GitHub Pages, custom domain, HTTPS) | Unreleased |

## What's next (v0.2)

Tracked in [TECH_DEBT.md](TECH_DEBT.md) and [post-1.0/backlog.md](post-1.0/backlog.md):

- **P1**: router-coupled items (probe cache, per-engine `avg_response_mb`, race billing) — items #1–#7.
- **P2 #11**: residential-proxy engine (`brightdata_unlocker`) for IP-geofenced targets.
- **P2 #12**: `scrape_sync` / `crawl_site_sync` wrappers robust to leaked event loops (Playwright Sync).
- **Engines**: `obscura`, `brightdata` family (see [post-1.0/backlog.md](post-1.0/backlog.md)).

## Cross-links

- [Architecture overview](architecture/overview.md) — modules, data flow, engine plug-points
- [Development workflow](workflows/development.md) — clone, install, run
- [Testing](workflows/testing.md) — markers (`paid`, `network`, `slow`), commands
- [Golden rules](conventions/golden-rules.md) — must-never-violate list
- [Agent mode](tools/agent-mode.md) — `--json`, no-color, fatal errors
- [Scripts](tools/scripts.md) — `check.sh`, `describe.sh`, `quick-test.sh`
- [CHANGELOG](../CHANGELOG.md) — version-by-version release notes
