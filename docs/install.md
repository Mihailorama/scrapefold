# scrapefold — agent setup instructions

You are an AI agent setting up **scrapefold** for your user. scrapefold turns
any URL into clean, LLM-ready markdown through one interface over 30 scraping
engines, with automatic anti-bot escalation. Follow the steps below **exactly
and in order**, then report what you installed and verified.

Ground rules:

- **Idempotent:** check before you change. Never reinstall what is installed,
  never overwrite an existing config — only add to it.
- **Honest:** if a step fails, say which step and show the error. Do not
  pretend a step succeeded.

## 1. Check if scrapefold is already installed

```bash
scrapefold --version
```

If this prints a version, scrapefold is installed — **do NOT reinstall**,
skip to step 3.

## 2. Install

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

## 3. Verify the CLI

```bash
scrapefold doctor
scrapefold scrape https://example.com
```

`doctor` prints the version, whether the MCP extra is present, and per-engine
availability. The second command should print markdown for the page. All
subcommands:

```bash
scrapefold scrape <url> [--engines jina,firecrawl] [--json] [--output page.md]
scrapefold crawl <url> [--max-pages 50] [--output site.md] [--per-page-dir ./pages/]
scrapefold list-engines [--json]
scrapefold classify <url> [--json]
scrapefold install [claude|codex|cursor|vscode|generic]
scrapefold doctor [--json]
scrapefold update [--check] [--extras mcp]
```

`--json` makes every command emit a single machine-parseable JSON document.
Errors are fatal with a non-zero exit code.

## 4. Register the MCP server (one command)

Pick the client you are running in:

```bash
scrapefold install claude    # Claude Code  (runs: claude mcp add scrapefold -- scrapefold-mcp)
scrapefold install codex     # Codex CLI    (runs: codex mcp add scrapefold -- scrapefold-mcp)
scrapefold install cursor    # Cursor       (merges ~/.cursor/mcp.json)
scrapefold install vscode    # VS Code      (runs: code --add-mcp ...)
scrapefold install generic   # prints the JSON below for any other client
```

`scrapefold install` is safe to re-run: it merges configs in place,
preserves every existing server, and is a no-op if scrapefold is already
registered.

If your client is not listed, **read the client's MCP config first, then add
this entry to it — preserve all existing content**:

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

## 5. Verify the MCP server

Tell the user to restart the client (MCP servers load on startup). After
restart, confirm the `scrapefold` server is connected and call
`list_engines` — it should return engine names such as `requests`, `jina`,
`firecrawl`.

## 6. Report what you did

End with a short setup report:

- Installed scrapefold, or found version X already installed (step 1/2)
- `scrapefold doctor` result: N/M engines importable, MCP extra yes/no (step 3)
- Which client you registered the MCP server in, and how (step 4)
- Whether the MCP connection was verified, or that a client restart is
  still pending (step 5)

## 7. Python API (optional)

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
