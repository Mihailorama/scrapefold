# scrapefold — agent setup instructions

You are an AI agent setting up **scrapefold** for your user. scrapefold turns
any URL into clean, LLM-ready markdown through one interface over 27 scraping
engines, with automatic anti-bot escalation. Follow the steps below, then
report what you installed and verified.

## 1. Install

```bash
pip install "scrapefold[mcp]"
```

Notes:
- Requires Python >= 3.10.
- `[mcp]` includes the MCP server. Plain `pip install scrapefold` is enough
  for CLI/library-only use.
- Vendor engines are optional extras, e.g.
  `pip install "scrapefold[firecrawl,scrapingbee]"` or `"scrapefold[all]"`.
  The free local engines work with no extras and no API keys.

## 2. Verify the CLI

```bash
scrapefold --version
scrapefold scrape https://example.com
```

The second command should print markdown for the page. All subcommands:

```bash
scrapefold scrape <url> [--engines jina,firecrawl] [--json] [--output page.md]
scrapefold crawl <url> [--max-pages 50] [--output site.md] [--per-page-dir ./pages/]
scrapefold list-engines [--json]
scrapefold classify <url> [--json]
scrapefold install [claude|codex|cursor|vscode|generic]
```

`--json` makes every command emit a single machine-parseable JSON document.
Errors are fatal with a non-zero exit code.

## 3. Register the MCP server (one command)

Pick the client you are running in:

```bash
scrapefold install claude    # Claude Code  (runs: claude mcp add scrapefold -- scrapefold-mcp)
scrapefold install codex     # Codex CLI    (runs: codex mcp add scrapefold -- scrapefold-mcp)
scrapefold install cursor    # Cursor       (merges ~/.cursor/mcp.json)
scrapefold install vscode    # VS Code      (runs: code --add-mcp ...)
scrapefold install generic   # prints the JSON below for any other client
```

If your client is not listed, add this to its MCP settings:

```json
{
  "mcpServers": {
    "scrapefold": {
      "command": "scrapefold-mcp",
      "args": []
    }
  }
}
```

The server speaks stdio and exposes four tools:

| Tool | Purpose |
|---|---|
| `scrape_url(url, engines?, render_js?, stealth?)` | Single URL → markdown (router auto-escalates engines) |
| `crawl_site(url, max_pages?)` | Whole site → stitched markdown |
| `list_engines()` | Registered engine names |
| `classify_url(url)` | SiteClass the router would assign |

## 4. Verify the MCP server

Restart the client if needed, then confirm the `scrapefold` server is
connected and call `list_engines` — it should return engine names such as
`requests`, `jina`, `firecrawl`.

## 5. Python API (optional)

```python
import asyncio
from scrapefold import scrape, crawl_site, ScrapeOptions

async def main():
    result = await scrape("https://example.com")
    print(result.markdown)

asyncio.run(main())
```

## Links

- Website: https://scrapefold.com/
- GitHub: https://github.com/mihailorama/scrapefold
- PyPI: https://pypi.org/project/scrapefold/
- Agent docs: https://github.com/mihailorama/scrapefold/blob/main/docs/tools/agent-mode.md
