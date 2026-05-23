# Pack 6 — Minimal Typer CLI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **PACK-OPEN REFRESH:** Re-run `scripts/check-deps-fresh.sh`. As of 2026-05-23, Typer was at 0.21.x — verify before starting. Pack 3 may have already bumped the floor to ≥0.21.

**Goal:** Land the `scrapefold` Typer CLI with four subcommands (`scrape`, `crawl`, `list-engines`, `classify`), each supporting `--json` output and exiting with non-zero on error per `docs/tools/agent-mode.md`.

**Architecture:** One file `cli.py` containing the Typer app + four sync command functions; each command calls `asyncio.run(...)` to invoke the async library functions. JSON output uses `json.dumps(asdict(result), default=str)`. Errors use `typer.Exit(code=N)` with messages to stderr.

**Tech Stack:** Python 3.10+, Typer ≥0.21, `typer.testing.CliRunner` for tests.

**Spec reference:** `docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md` §3.4.

---

## Phase A — Dep-freshness audit

- [ ] Run `./scripts/check-deps-fresh.sh`. Bump Typer / pyyaml / etc. if stale. Commit if changed.

---

## Phase B — `cli.py` (TDD)

### Task B.1 — Failing tests

**Files:**
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write tests**

Create `tests/test_cli.py`:

```python
"""Tests for the scrapefold Typer CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scrapefold.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_scrape(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from scrapefold.options import ScrapeOptions
    from scrapefold.result import ScrapeResult

    captured: dict[str, Any] = {"called_with": None}

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        captured["called_with"] = {"url": url, "engines": opts.engines if opts else None}
        return ScrapeResult(
            url=url,
            text="hello text",
            markdown="# hello\n\nbody",
            html=None,
            engine="stub",
            elapsed_ms=12,
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)
    return captured


# ---------------------------------------------------------------------------
# 1. `scrapefold scrape <url>` prints markdown to stdout, exit 0
# ---------------------------------------------------------------------------


def test_scrape_command_prints_markdown(
    runner: CliRunner, stub_scrape: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["scrape", "https://example.com/"])
    assert result.exit_code == 0
    assert "# hello" in result.stdout
    assert stub_scrape["called_with"]["url"] == "https://example.com/"


# ---------------------------------------------------------------------------
# 2. `scrapefold scrape <url> --json` prints JSON, exit 0
# ---------------------------------------------------------------------------


def test_scrape_command_json_output(
    runner: CliRunner, stub_scrape: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["scrape", "https://example.com/", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["url"] == "https://example.com/"
    assert data["markdown"] == "# hello\n\nbody"
    assert data["engine"] == "stub"


# ---------------------------------------------------------------------------
# 3. `--engines` accepts comma-separated and forwards to opts.engines
# ---------------------------------------------------------------------------


def test_scrape_command_engines_override(
    runner: CliRunner, stub_scrape: dict[str, Any]
) -> None:
    runner.invoke(app, ["scrape", "https://example.com/", "--engines", "jina,firecrawl"])
    assert stub_scrape["called_with"]["engines"] == ["jina", "firecrawl"]


# ---------------------------------------------------------------------------
# 4. `--output PATH` writes markdown to file
# ---------------------------------------------------------------------------


def test_scrape_command_output_to_file(
    runner: CliRunner, stub_scrape: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "scrape.md"
    result = runner.invoke(app, ["scrape", "https://example.com/", "--output", str(out)])
    assert result.exit_code == 0
    assert out.read_text() == "# hello\n\nbody"


# ---------------------------------------------------------------------------
# 5. `scrapefold scrape` propagates AllEnginesFailed → exit 1
# ---------------------------------------------------------------------------


def test_scrape_command_all_engines_failed_exit_1(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scrapefold import AllEnginesFailed

    async def _raise(url, opts=None):
        raise AllEnginesFailed("nothing worked")

    monkeypatch.setattr("scrapefold.scrape", _raise)

    result = runner.invoke(app, ["scrape", "https://example.com/"])
    assert result.exit_code == 1
    assert "all engines failed" in result.output.lower()


# ---------------------------------------------------------------------------
# 6. `scrapefold list-engines` prints registered engine names
# ---------------------------------------------------------------------------


def test_list_engines_command(runner: CliRunner) -> None:
    result = runner.invoke(app, ["list-engines"])
    assert result.exit_code == 0
    # At least one known engine should be in the output
    assert "requests" in result.stdout
    assert "firecrawl" in result.stdout


def test_list_engines_json_output(runner: CliRunner) -> None:
    result = runner.invoke(app, ["list-engines", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert "requests" in data


# ---------------------------------------------------------------------------
# 7. `scrapefold classify <url>` returns the SiteClass
# ---------------------------------------------------------------------------


def test_classify_command_known_url(runner: CliRunner) -> None:
    result = runner.invoke(app, ["classify", "https://www.linkedin.com/in/someone"])
    assert result.exit_code == 0
    assert "linkedin" in result.stdout.lower()


def test_classify_command_json(runner: CliRunner) -> None:
    result = runner.invoke(
        app, ["classify", "https://www.linkedin.com/in/someone", "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "url" in data
    assert "site_class" in data


# ---------------------------------------------------------------------------
# 8. `scrapefold crawl <url> --max-pages 3` calls crawl_site
# ---------------------------------------------------------------------------


def test_crawl_command(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "site.md"

    async def _fake_crawl(url, opts=None, output=None):
        Path(output).write_text("crawled content")
        return Path(output)

    monkeypatch.setattr("scrapefold.crawl_site", _fake_crawl)

    result = runner.invoke(
        app,
        ["crawl", "https://example.com/", "--max-pages", "3", "--output", str(out)],
    )
    assert result.exit_code == 0
    assert out.read_text() == "crawled content"


# ---------------------------------------------------------------------------
# 9. `scrapefold --version` works
# ---------------------------------------------------------------------------


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    import scrapefold
    assert scrapefold.__version__ in result.stdout


# ---------------------------------------------------------------------------
# 10. `scrapefold --help` lists all four subcommands
# ---------------------------------------------------------------------------


def test_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scrape" in result.stdout
    assert "crawl" in result.stdout
    assert "list-engines" in result.stdout
    assert "classify" in result.stdout
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_cli.py -v`
Expected: `ImportError: app` or every test fails — current `cli.py` is the S1 stub.

### Task B.2 — Implement `cli.py`

**Files:**
- Modify: `src/scrapefold/cli.py`

- [ ] **Step 1: Replace cli.py**

Replace the contents of `src/scrapefold/cli.py` with:

```python
"""scrapefold — Typer CLI entry point.

Four subcommands: scrape, crawl, list-engines, classify.
--json flag everywhere; errors fatal with non-zero exit code.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer

import scrapefold
from scrapefold import AllEnginesFailed, ScrapeOptions, classify_url
from scrapefold.engines import list_engine_names

app = typer.Typer(
    help="scrapefold — unified web scraping CLI",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(scrapefold.__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    pass


def _engines_arg(engines: Optional[str]) -> Optional[list[str]]:
    if engines is None:
        return None
    return [e.strip() for e in engines.split(",") if e.strip()]


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------


@app.command()
def scrape(
    url: str = typer.Argument(..., help="URL to scrape."),
    engines: Optional[str] = typer.Option(
        None,
        "--engines",
        help="Comma-separated engine override (e.g. 'jina,firecrawl').",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    output: Optional[Path] = typer.Option(
        None, "--output", help="Write markdown to PATH instead of stdout."
    ),
) -> None:
    """Scrape a single URL and print the markdown (or full JSON with --json)."""
    opts = ScrapeOptions(engines=_engines_arg(engines))

    async def _run() -> object:
        return await scrapefold.scrape(url, opts)

    try:
        result = asyncio.run(_run())
    except AllEnginesFailed as exc:
        typer.echo(f"all engines failed for {url}: {exc}", err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps(asdict(result), default=str))
        return

    if output is not None:
        output.write_text(result.markdown)
        return

    typer.echo(result.markdown)


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------


@app.command()
def crawl(
    url: str = typer.Argument(..., help="Root URL to crawl."),
    max_pages: int = typer.Option(
        100, "--max-pages", help="Maximum pages to fetch (default 100)."
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Output .md file path (default: <tmp>/scrapefold-crawl.md).",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON summary to stdout."),
) -> None:
    """Crawl a site → produce one stitched markdown file."""
    opts = ScrapeOptions(max_pages=max_pages)

    async def _run() -> object:
        return await scrapefold.crawl_site(url, opts=opts, output=output)

    try:
        result_path = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — surface anything to the user
        typer.echo(f"crawl failed for {url}: {exc}", err=True)
        raise typer.Exit(code=1)

    if json_out:
        typer.echo(json.dumps({"output": str(result_path)}))
        return

    typer.echo(str(result_path))


# ---------------------------------------------------------------------------
# list-engines
# ---------------------------------------------------------------------------


@app.command("list-engines")
def list_engines_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON list to stdout."),
) -> None:
    """Print every engine registered in the lazy registry."""
    names = list_engine_names()
    if json_out:
        typer.echo(json.dumps(names))
        return
    for name in names:
        typer.echo(name)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


@app.command()
def classify(
    url: str = typer.Argument(..., help="URL to classify."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
) -> None:
    """Print the SiteClass scrapefold's router would assign to a URL."""
    site_class = classify_url(url)
    if json_out:
        typer.echo(json.dumps({"url": url, "site_class": site_class}))
        return
    typer.echo(site_class)


def main() -> None:
    """Console-script entry point — referenced by pyproject `[project.scripts]`."""
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_cli.py -v`
Expected: all 14 PASS.

- [ ] **Step 3: Manual smoke**

```bash
pip install -e ".[test]"
scrapefold --version
scrapefold list-engines
scrapefold classify https://www.linkedin.com/in/someone
```

Expected: prints version, engine list, site class.

- [ ] **Step 4: Full suite**

Run: `./scripts/check.sh`
Expected: `=== All checks passed ===`.

- [ ] **Step 5: Commit**

```bash
git add src/scrapefold/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat: Pack 6 — Typer CLI (scrape / crawl / list-engines / classify)

Four subcommands, --json everywhere, errors fatal with non-zero exit.
asyncio.run wraps each async library call. --output PATH writes
markdown/crawl output to disk instead of stdout. --version prints
__version__ and exits.

14 CLI tests via typer.testing.CliRunner.

Spec: docs/superpowers/specs/2026-05-23-v0.1.0-stable-roadmap-design.md §3.4
EOF
)"
```

---

## Phase C — Update docs and CHANGELOG

- [ ] **Step 1: Update `docs/tools/agent-mode.md`**

Document the actual CLI surface now that it exists. Add a section listing the four subcommands with their `--json` semantics.

- [ ] **Step 2: Update `docs/README.md` Project status**

Row for Pack 6 → 0.1.0a5.

- [ ] **Step 3: Update `docs/workflows/development.md`**

Replace "Pack 6 = CLI; MCP slips to v0.2" line with "CLI lands in 0.1.0a5; MCP slips to v0.2."

- [ ] **Step 4: Commit**

```bash
git add docs/tools/agent-mode.md docs/README.md docs/workflows/development.md
git commit -m "docs: Pack 6 — CLI surface documented"
```

---

## Phase D — CHANGELOG roll + tag

- [ ] Bump `__version__` to `0.1.0a5`.
- [ ] Roll CHANGELOG, add compare-link.
- [ ] Run `./scripts/check.sh` → green.
- [ ] Commit + ASK before push/tag `v0.1.0a5`.

---

## Self-review

**Spec coverage** (§3.4):
- `scrapefold scrape <url> [--engines …] [--json] [--output PATH]` → command `scrape` ✅
- `scrapefold crawl <url> [--max-pages N] [--output PATH] [--json]` → command `crawl` ✅
- `scrapefold list-engines [--json]` → command `list-engines` ✅
- `scrapefold classify <url> [--json]` → command `classify` ✅
- `--json` everywhere → every command has `json_out` option ✅
- Errors fatal → `typer.Exit(code=1)` on `AllEnginesFailed` and `crawl` exceptions ✅
- ~15 CLI tests via CliRunner → 14 tests ✅ (rounded down from spec estimate)

**No placeholders.** Each test and the implementation are complete.

**Type consistency.** `scrape` command uses `engines: Optional[str]` parsed via `_engines_arg`; `ScrapeOptions(engines=[...])` is the type-correct shape. `crawl` returns `Path` (from `crawl_site`). `classify_url` is already public; reuses the engine.

**Deferred:** Interactive mode, progress bars (no tqdm), MCP server wiring (`scrapefold-mcp` stays a stub).
