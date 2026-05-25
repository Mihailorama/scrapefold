---
purpose: "Local dev workflow — clone, install, run, lint."
updated: "2026-05-22"
related:
  - testing.md
  - ../conventions/golden-rules.md
---

# Development workflow

## Prerequisites

- Python ≥ 3.10 (3.11 recommended, see `.python-version`)
- `uv` or `pip` for env management

## Clone + install

```bash
git clone https://github.com/mihailorama/scrapefold.git
cd scrapefold

# Editable install with test deps
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

To install with every engine extras for local benchmarking:

```bash
pip install -e ".[all]"
```

## Run the CLI

```bash
scrapefold --help
scrapefold list-engines
scrapefold classify https://www.linkedin.com/in/someone

# Scrape a URL
scrapefold scrape https://example.com
scrapefold scrape https://example.com --json | jq '.engine'

# Crawl a site
scrapefold crawl https://docs.example.com --output site.md --max-pages 50
scrapefold crawl https://docs.example.com --per-page-dir ./pages/
```

CLI lands in 0.1.0a5; MCP server (`scrapefold-mcp`) slips to v0.2.

## Run the MCP server

```bash
pip install -e ".[mcp]"
scrapefold-mcp     # stub in S1; full server in S10
```

## Lint / type-check / test

One-shot pre-commit gate:

```bash
./scripts/check.sh
```

Individual steps:

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -m "not paid and not network"
```

## Project state snapshot

```bash
./scripts/describe.sh
```

Prints structure, last 5 commits, failing tests, TODOs.

## Adding a new engine

See [../../CONTRIBUTING.md](../../CONTRIBUTING.md) § "Add an engine". Five-step checklist: subclass `ScrapeEngine`, set NAME/CAPABILITIES/SUPPORTED_OPTIONS, implement `_fetch`, register in `engines/__init__.py`, add tests under `tests/test_engines/`.

## Env vars (used by engines, NOT loaded by scrapefold itself)

| Var | Engine |
|---|---|
| `FIRECRAWL_API_KEY` | firecrawl |
| `SCRAPINGBEE_API_KEY` | scrapingbee |
| `SCRAPINGDOG_API_KEY` | scrapingdog |
| `JINA_API_KEY` | jina |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | cloudflare |
| `OUTSCRAPER_API_KEY` | outscraper |
| `APIFY_API_TOKEN` | apify |
| `ANYSITE_API_KEY` | anysite |
| `BRIGHTDATA_API_KEY` | brightdata |

scrapefold does not call `load_dotenv()`. Set them in your shell or use your own loader.
