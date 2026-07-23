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

### `scrapefold doctor`

Post-install health check — version, Python, MCP extra presence, and
per-engine availability (which optional extras are importable):

```bash
scrapefold doctor            # human-readable
scrapefold doctor --json     # {"version": ..., "mcp_extra": true, "engines": {...}}
```

### `scrapefold update`

Self-update via the running interpreter's pip:

```bash
scrapefold update --check           # compare installed vs latest on PyPI
scrapefold update --check --json    # {"current": ..., "latest": ..., "update_available": ...}
scrapefold update                   # pip install --upgrade scrapefold
scrapefold update --extras mcp      # upgrade as scrapefold[mcp]
```

### `scrapefold install [client]`

One-click MCP registration — see [MCP server → One-click registration](#one-click-registration) below.

```bash
scrapefold install claude          # register in Claude Code
scrapefold install --json          # print mcpServers config JSON
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

`scrapefold-mcp` is a stdio MCP server built on the official `mcp` SDK
(FastMCP). Without the `mcp` extra installed it exits with code 2 and an
install hint.

```bash
pip install "scrapefold[mcp]"
scrapefold-mcp                          # stdio transport
```

### One-click registration

`scrapefold install <client>` registers the server for you:

```bash
scrapefold install claude    # runs: claude mcp add scrapefold -- scrapefold-mcp
scrapefold install codex     # runs: codex mcp add scrapefold -- scrapefold-mcp
scrapefold install cursor    # merges ~/.cursor/mcp.json in place
scrapefold install vscode    # runs: code --add-mcp '{"name":"scrapefold",...}'
scrapefold install generic   # prints the mcpServers JSON for any client
scrapefold install --json    # emits the config JSON alone (machine-parseable)
scrapefold install claude --print-only   # show the command without executing
```

If the client's own CLI (`claude` / `codex` / `code`) is not on PATH, the
command to run is printed instead of executed — nothing fails silently.

### Agent setup prompt

Agents can self-install from a single fetchable instruction page:

```
fetch https://scrapefold.com/install.md
```

(Source: [docs/install.md](../install.md), served raw via GitHub Pages —
`docs/.nojekyll` keeps `.md` files unprocessed. `https://scrapefold.com/llms.txt`
indexes it for LLM crawlers.)

### Manual configuration

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

Exposed tools:

| Tool | Args | Returns |
|---|---|---|
| `scrape_url` | `url`, `engines?` (comma-separated), `render_js?`, `stealth?`, `focus?` | `ScrapeResult` JSON (screenshot dropped, oversized `html` nulled). With `focus="query"`, only BM25-relevant markdown blocks are returned — a fraction of the tokens of the full page |
| `crawl_site` | `url`, `max_pages?` (default 25) | `{url, pages, markdown, failures}` — `markdown` is the stitched crawl |
| `list_engines` | — | engine name list |
| `classify_url` | `url` | `{url, site_class}` |

Error honesty: when every engine fails, `scrape_url` returns a structured
`{"url", "error": "all engines failed", "failures": [...]}` — never an
error page posing as content. (The router itself never returns a
suspicious/blocked page: it escalates, and raises when the ladder is
exhausted.)

Token cost: all 4 tool definitions + server instructions ≈ **750 tokens**
(estimate, chars/4) — pinned by a budget test so they stay tight.

Planned (not yet implemented): `inspect_options` tool, `scrapefold://cache/{sha}`
and `scrapefold://engines` resources.

## Why no `--dry-run` flag

scrapefold has no side effects on the local system other than writing cache files and (for `crawl`) an output markdown file. There's nothing to "simulate" — pass `--cache-dir /tmp/x` and `--output /dev/null` if you need a no-trace run.
