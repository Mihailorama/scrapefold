---
purpose: "How to use scrapefold from AI agents — machine-parseable output, fatal errors."
updated: "2026-05-25"
related:
  - ../../README.md
  - ../workflows/development.md
---

# Agent mode

scrapefold is built to be driven by AI agents (Claude Code, Codex, Cursor) as well as humans. Two interfaces:

1. **CLI** — `scrapefold` console script, Typer-based, `--json` flag everywhere.
2. **MCP server** — `scrapefold-mcp` console script, stdio transport by default.

## CLI subcommands (v0.1.0a5)

### `scrapefold scrape <url>`

Scrape a single URL and print its markdown (or JSON with `--json`).

```bash
# Print markdown to stdout
scrapefold scrape https://example.com

# Machine-parseable JSON
scrapefold scrape https://example.com --json | jq '.markdown'

# Force specific engine(s)
scrapefold scrape https://example.com --engines jina,firecrawl

# Write markdown to file
scrapefold scrape https://example.com --output page.md
```

JSON output shape: `ScrapeResult` as dict — `url`, `text`, `markdown`, `html`, `engine`, `elapsed_ms`, `cost_usd`, `json`, `screenshot_b64`, `meta`, `failures`.

### `scrapefold crawl <url>`

Crawl a site (BFS up to `--max-pages`) and optionally write a stitched markdown file.

```bash
# Stitched output to file
scrapefold crawl https://docs.example.com --output site.md --max-pages 50

# Per-page .md files in a directory (one file per crawled URL)
scrapefold crawl https://docs.example.com --per-page-dir ./pages/

# Both: stitched file + per-page dir
scrapefold crawl https://docs.example.com --output site.md --per-page-dir ./pages/

# JSON summary
scrapefold crawl https://docs.example.com --output site.md --json
```

`--per-page-dir DIR`: writes `<sha256(url)[:16]>.md` for each page; prints one `wrote <path> (<url>)` line to stderr per file. Designed for feeding per-URL markdown into downstream parsers.

### `scrapefold list-engines`

Print every engine registered in the lazy registry.

```bash
scrapefold list-engines
scrapefold list-engines --json | jq '.[]'
```

### `scrapefold classify <url>`

Print the `SiteClass` the router would assign to a URL.

```bash
scrapefold classify https://www.linkedin.com/in/someone
# → linkedin_profile

scrapefold classify https://www.linkedin.com/in/someone --json
# → {"url": "...", "site_class": "linkedin_profile"}
```

## CLI agent flags

| Flag | Behavior |
|---|---|
| `--json` | Output is a single JSON document on stdout, no ANSI colors, no spinners |
| `--engines` | Comma-separated engine override for `scrape` (e.g. `jina,firecrawl`) |
| `--output PATH` | Write markdown / stitched crawl file to disk instead of stdout |
| `--per-page-dir DIR` | (`crawl` only) Write one `.md` per crawled page; path = `sha256(url)[:16].md` |
| `--max-pages N` | (`crawl` only) Page limit, default 100 |
| (default) | Errors are fatal — non-zero exit code + clear message to stderr |

Examples:

```bash
scrapefold scrape https://example.com --engines requests --json | jq '.text'
scrapefold list-engines --json
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | All engines in chain failed |
| 2 | Invalid options or unknown engine |
| 3 | Cache / IO error |

## MCP server

`scrapefold-mcp` exists today as a console-script scaffold and exits with a
non-zero status until the S10 MCP implementation lands. The contract below is
the target implementation.

```bash
pip install "scrapefold[mcp]"
scrapefold-mcp                          # stdio, default
scrapefold-mcp --http :8765             # HTTP/SSE
```

Drop this into `~/.claude/mcp.json` or a project-local `.mcp.json`:

```json
{
  "mcpServers": {
    "scrapefold": {
      "command": "scrapefold-mcp",
      "args": [],
      "env": {
        "FIRECRAWL_API_KEY": "fc-...",
        "BRIGHTDATA_API_KEY": "..."
      }
    }
  }
}
```

Exposed tools (S10):

| Tool | Args | Returns |
|---|---|---|
| `scrape_url` | `url` + flattened `ScrapeOptions` fields | `ScrapeResult` JSON |
| `crawl_site` | `url`, `max_pages`, `max_depth`, `strategy`, `engines`, `output_path?` | `{output_path, pages_fetched, …}` |
| `list_engines` | — | engine table with availability flags |
| `inspect_options` | `engine` | supported opts + adapter mapping |

Resources (read-only):

- `scrapefold://cache/{sha}` — cached scrape result without re-scraping
- `scrapefold://engines` — same as `list_engines` as a static resource

## Why no `--dry-run` flag

scrapefold has no side effects on the local system other than writing cache files and (for `crawl`) an output markdown file. There's nothing to "simulate" — pass `--cache-dir /tmp/x` and `--output /dev/null` if you need a no-trace run.
