---
purpose: "Documentation index — start here. Navigate to architecture, workflows, and conventions."
updated: "2026-05-22"
related:
  - architecture/overview.md
  - workflows/development.md
  - conventions/golden-rules.md
---

# scrapefold docs

Unified Python library for web scraping — single URL or whole-site → markdown, with stealth, JS rendering, and LLM-ready output. Wraps 10 vendor APIs + local stealth browsers + a baseline `requests` engine behind one async interface.

## Where to look

| If you want to… | Start at |
|---|---|
| Understand the architecture | [architecture/overview.md](architecture/overview.md) |
| Set up a dev environment | [workflows/development.md](workflows/development.md) |
| Run tests | [workflows/testing.md](workflows/testing.md) |
| Know what rules the AI / contributors must follow | [conventions/golden-rules.md](conventions/golden-rules.md) |
| Understand agent mode (machine-friendly output) | [tools/agent-mode.md](tools/agent-mode.md) |
| Add a new engine | [../CONTRIBUTING.md](../CONTRIBUTING.md) |

## Project status

**v0.1.0a5 — CLI landed.** Engines and CLI complete; MCP server next:

| PR / Pack | Engines / features | Version |
|---|---|---|
| S1 ✅ | Scaffold: pyproject, base ABC, options/result, CLI/MCP stubs, docs | 0.1.0a0 |
| Pack 2A-C ✅ | `requests`, Jina, Cloudflare, Crawl4AI, Firecrawl, ScrapingBee, Scrapingdog, Selenium, Scrapling, Apify-LinkedIn, Outscraper, AnySite, Cloakbrowser | 0.1.0a1 |
| Pack 3 ✅ | Router + ladders + parallel + detection | 0.1.0a2 |
| Pack 4 ✅ | html_to_text + vision | 0.1.0a3 |
| Pack 5 ✅ | Crawler (sitemap → BFS → stitch → cache) + `CrawlResult` | 0.1.0a4 |
| Pack 6 ✅ | CLI (Typer): `scrape`, `crawl`, `list-engines`, `classify` + `--per-page-dir` | 0.1.0a5 |
| Pack 7 | MCP server (`scrapefold-mcp`, stdio + HTTP) | 0.2.0 |
| Pack 8 | v0.1.0 release + PyPI publish | 0.1.0 |

## Cross-links

- [Architecture overview](architecture/overview.md) — modules, data flow, engine plug-points
- [Development workflow](workflows/development.md) — clone, install, run
- [Testing](workflows/testing.md) — markers (`paid`, `network`, `slow`), commands
- [Golden rules](conventions/golden-rules.md) — must-never-violate list
- [Agent mode](tools/agent-mode.md) — `--json`, no-color, fatal errors
- [Scripts](tools/scripts.md) — `check.sh`, `describe.sh`, `quick-test.sh`
