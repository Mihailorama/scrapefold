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

**v0.1.0a0 — scaffold.** Engines land incrementally:

| PR | Engines / features |
|---|---|
| S1 ✅ | Scaffold: pyproject, base ABC, options/result, CLI/MCP stubs, docs |
| S2 | `requests` baseline + html_to_text + url_utils |
| S3 | Jina + Cloudflare + Crawl4AI + Outscraper |
| S4 | Firecrawl (scrape + extract) |
| S5 | ScrapingBee + Scrapingdog + Selenium |
| S6 | Scrapling + Apify-LinkedIn |
| S7 | Router + parallel + LLM-judge |
| S8 | Crawler (sitemap → BFS → stitch → cache) |
| S9 | CLI (Typer) |
| S10 | MCP server |
| S11a / S11b | Obscura, Cloakbrowser, AnySite, Bright Data (Unlocker + Browser) |
| S12 | v0.1.0 release |

## Cross-links

- [Architecture overview](architecture/overview.md) — modules, data flow, engine plug-points
- [Development workflow](workflows/development.md) — clone, install, run
- [Testing](workflows/testing.md) — markers (`paid`, `network`, `slow`), commands
- [Golden rules](conventions/golden-rules.md) — must-never-violate list
- [Agent mode](tools/agent-mode.md) — `--json`, no-color, fatal errors
- [Scripts](tools/scripts.md) — `check.sh`, `describe.sh`, `quick-test.sh`
