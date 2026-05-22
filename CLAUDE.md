# scrapefold

Unified Python library for web scraping — single URL or whole-site → markdown, with stealth, JS rendering, and LLM-ready output.

## Quick Start

```bash
# Run tests (offline)
pytest -m "not paid and not network"

# Pre-commit gate
./scripts/check.sh

# Project state snapshot
./scripts/describe.sh
```

## Key Files

| File / Dir | Purpose |
|---|---|
| [docs/](docs/) | Full documentation graph — start with [docs/README.md](docs/README.md) |
| [docs/conventions/golden-rules.md](docs/conventions/golden-rules.md) | Rules you MUST follow |
| [docs/architecture/overview.md](docs/architecture/overview.md) | Module map, data flow, escalation ladder, result format slots |
| [docs/workflows/development.md](docs/workflows/development.md) | Dev env setup |
| [docs/workflows/testing.md](docs/workflows/testing.md) | Test markers, layered plan |
| [docs/tools/agent-mode.md](docs/tools/agent-mode.md) | CLI / MCP for AI agents |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to add a new engine (5-step checklist) |
| `src/scrapefold/options.py` | `ScrapeOptions` — unified parameter schema |
| `src/scrapefold/result.py` | `ScrapeResult` — text/markdown/html/json slots |
| `src/scrapefold/engines/base.py` | `ScrapeEngine` ABC + `EngineCapabilities` |
| `scripts/check.sh` | Lint + type-check + offline tests |

## Golden Rules (top 5)

1. **One unified options schema** — every engine takes the same `ScrapeOptions`.
2. **Engines drop unsupported options, never raise** — `SUPPORTED_OPTIONS` set + DEBUG log.
3. **All four format slots are populated when achievable** — `text`+`markdown` always, `html`/`json` when native.
4. **Escalate cheap-to-expensive, stop at first good response** — T0→T1→T2→T3→T4 ladder with stop rules.
5. **No vendor LLM SDK in the library** — LLM passed as a user-provided async callable.

_Full rules: [docs/conventions/golden-rules.md](docs/conventions/golden-rules.md)_

## Architecture (one-liner)

`scrape(url, opts) → ScrapeRouter walks an escalation ladder of engines, each implementing `ScrapeEngine._fetch(url, opts) → ScrapeResult`, until a non-suspicious response is returned.

_Full details: [docs/architecture/overview.md](docs/architecture/overview.md)_
