---
purpose: "How to use scrapefold from AI agents — machine-parseable output, fatal errors."
updated: "2026-05-22"
related:
  - ../../README.md
  - ../workflows/development.md
---

# Agent mode

scrapefold is built to be driven by AI agents (Claude Code, Codex, Cursor) as well as humans. Two interfaces:

1. **CLI** — `scrapefold` console script, Typer-based, `--json` flag everywhere.
2. **MCP server** — `scrapefold-mcp` console script, stdio transport by default.

## CLI agent flags

| Flag | Behavior |
|---|---|
| `--json` | Output is a single JSON document on stdout, no ANSI colors, no spinners |
| `--quiet` | Suppress non-error logging |
| `--fail-fast` | Exit on first engine failure rather than walking the fallback chain |
| (default) | Errors are fatal — non-zero exit code + clear message to stderr |

Examples once S9 ships:

```bash
scrapefold scrape https://example.com --engine requests --json | jq '.text'
scrapefold list-engines --json | jq '.[] | select(.available)'
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | All engines in chain failed |
| 2 | Invalid options or unknown engine |
| 3 | Cache / IO error |

## MCP server

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
