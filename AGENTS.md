# AGENTS.md

Entry point for non-Claude AI agents (Codex, Cursor, custom orchestrators) working on this repo.

This file mirrors [CLAUDE.md](CLAUDE.md) — pointers, not content. Read the docs/ graph for everything.

## What this project is

`scrapefold` — unified Python library for web scraping with one async API across 16 engines and a built-in anti-bot escalation ladder.

## Start here

1. [docs/README.md](docs/README.md) — index of all docs
2. [docs/conventions/golden-rules.md](docs/conventions/golden-rules.md) — the constraints
3. [CONTRIBUTING.md](CONTRIBUTING.md) — how to add an engine

## Run / test / lint

```bash
pip install -e ".[test]"
./scripts/check.sh                              # lint + type-check + offline tests
pytest -m "not paid and not network"            # default suite
```

## Conventions

- All code under `src/scrapefold/`. Tests under `tests/`. Docs under `docs/`. Scripts under `scripts/`.
- Async-everywhere. `httpx` for HTTP, `asyncio` for concurrency. No `requests` package.
- No vendor LLM SDK imports anywhere in `src/`. LLM is an injected callable.
- Engines are lazy-imported via `src/scrapefold/engines/__init__.py` registry.

## Output

Default CLI output is human-readable. Pass `--json` everywhere for machine-parseable output. Errors are fatal (non-zero exit + clear stderr message).
