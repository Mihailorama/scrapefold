# scrapefold

> Unified Python library for web scraping — single URL or whole-site → markdown, with stealth, JS rendering, and LLM-ready output. Wraps 16 vendor APIs and local stealth browsers behind one async interface.

**Status:** v0.1.0a0 — scaffold. Engines land incrementally; see [docs/README.md](docs/README.md) for the roadmap.

## Why

The web is hostile. A real scraping pipeline has to cascade through cheap-and-fast → stealth-browser → paid-residential-proxy until something works. Hand-rolling that cascade per project means 2000 LOC of glue code per repo. scrapefold gives you one async call:

```python
from scrapefold import scrape, ScrapeOptions

res = await scrape("https://example.com")
res.text       # always
res.markdown   # always
res.html       # when the engine returned HTML
res.json       # when the engine returned structured data
```

The same call works against a static blog (one `requests` call, ~200 ms, $0) and against a Datadome-protected site (auto-escalates through Scrapling → Cloakbrowser → Firecrawl → Bright Data Unlocker, stops at the first one that succeeds).

## Install

```bash
pip install scrapefold                      # core + baseline requests engine
pip install "scrapefold[firecrawl]"         # one specific vendor
pip install "scrapefold[all]"               # everything
pip install "scrapefold[mcp]"               # for the MCP server
```

## Quick start

```python
import asyncio
from scrapefold import scrape, crawl_site, ScrapeOptions

async def main():
    # Single URL, auto-engine
    res = await scrape("https://example.com")
    print(res.markdown)

    # Russian-domain example — same opts work for every engine
    opts = ScrapeOptions(language="ru", country="ru", render_js=True, stealth=True)
    res = await scrape("https://lenta.ru", opts=opts)

    # Whole site → one big markdown file
    await crawl_site(
        "https://docs.example.com",
        opts=ScrapeOptions(max_pages=50, max_depth=3),
        output="site.md",
        cache_dir="~/.scrapefold/cache",
        cache_ttl_hours=24,
    )

asyncio.run(main())
```

## CLI

```bash
scrapefold scrape https://example.com --engine firecrawl --language ru --json
scrapefold crawl https://docs.example.com --max-pages 50 --output site.md
scrapefold list-engines
scrapefold inspect-opts firecrawl
```

## MCP server (for Claude Code, Cursor, agents)

```bash
pip install "scrapefold[mcp]"
scrapefold-mcp
```

Drop into `~/.claude/mcp.json`:

```json
{ "mcpServers": { "scrapefold": { "command": "scrapefold-mcp", "args": [] } } }
```

Exposes `scrape_url`, `crawl_site`, `list_engines`, `inspect_options` tools and `scrapefold://cache/*`, `scrapefold://engines` resources.

## Engines (v0.1, 16 total)

**Local (free, no key):** `requests`, `scrapling`, `crawl4ai`, `cloakbrowser`, `obscura`, `selenium` (deprecated).

**SaaS (paid):** `firecrawl`, `scrapingbee`, `scrapingdog`, `jina`, `cloudflare`, `outscraper`, `apify_linkedin`, `anysite`, `brightdata_unlocker`, `brightdata_browser`.

See [docs/architecture/overview.md § Anti-bot escalation ladder](docs/architecture/overview.md#anti-bot-escalation-ladder) for the full cascade.

## Documentation

- [docs/README.md](docs/README.md) — index
- [docs/architecture/overview.md](docs/architecture/overview.md) — module map, data flow, escalation ladder
- [docs/workflows/development.md](docs/workflows/development.md) — clone, install, run
- [docs/workflows/testing.md](docs/workflows/testing.md) — marker strategy
- [docs/conventions/golden-rules.md](docs/conventions/golden-rules.md) — invariants
- [docs/tools/agent-mode.md](docs/tools/agent-mode.md) — `--json`, MCP server
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to add a new engine

## License

MIT — see [LICENSE](LICENSE).
