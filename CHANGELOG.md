# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning follows [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.0a0] — 2026-05-22

### Added — S1 scaffold

- Package layout: `src/scrapefold/` with `options.py`, `result.py`, `engines/base.py`, lazy engine registry.
- `ScrapeOptions` dataclass — single unified parameter schema (language, country, render_js, wait_ms, stealth, premium_proxy, user_agent, custom_headers, cookies, output_format, take_screenshot, max_pages, max_depth, engines, parallel, timeout_s, skip_cache, extra).
- `ScrapeResult` dataclass — four format slots (`text`, `markdown`, `html`, `json`) + `screenshot_b64`, `meta`, `failures`, `elapsed_ms`, `cost_usd`.
- `ScrapeEngine` ABC with `EngineCapabilities` + `EngineError`. Base class handles option-stripping (DEBUG log, never raises), timing, error wrapping.
- Public `scrape()` and `crawl_site()` stubs in `__init__.py` (raise `NotImplementedError` until S7/S8).
- CLI scaffold (`scrapefold` console script via Typer).
- MCP server scaffold (`scrapefold-mcp` console script).
- Docs graph per HARNESS_BOOTSTRAP: `docs/README.md`, `docs/architecture/overview.md`, `docs/workflows/{development,testing}.md`, `docs/conventions/golden-rules.md`, `docs/tools/{agent-mode,scripts}.md`.
- Anti-bot escalation ladder documented (T0→T4 + stop rules + suspicious-content detection heuristics planned in `detection.py`).
- Golden rules: unified options, options-dropping-never-raises, ScrapeResult invariant (all four slots), escalate-and-stop, suspicious-detection-in-one-place, no vendor LLM SDK, lazy engine imports, async everywhere.
- Helper scripts: `scripts/check.sh`, `scripts/describe.sh`, `scripts/quick-test.sh`.
- GitHub Actions CI: lint + type-check + offline tests on Python 3.10/3.11/3.12; PyPI publish via trusted publishing on `v*` tag; opt-in `paid` and `network` test jobs via `workflow_dispatch`.
- Smoke tests + `ScrapeEngine` ABC contract tests.

[Unreleased]: https://github.com/mihailorama/scrapefold/compare/v0.1.0a0...HEAD
[0.1.0a0]: https://github.com/mihailorama/scrapefold/releases/tag/v0.1.0a0
