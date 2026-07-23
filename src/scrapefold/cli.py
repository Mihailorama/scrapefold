"""scrapefold — Typer CLI entry point.

Six subcommands: scrape, crawl, list-engines, classify, install, doctor.
--json flag everywhere; errors fatal with non-zero exit code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

import scrapefold
from scrapefold import AllEnginesFailed, ScrapeOptions, classify_url
from scrapefold.engines import list_engine_names
from scrapefold.result import ScrapeResult

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


def _engines_arg(engines: str | None) -> tuple[str, ...] | None:
    if engines is None:
        return None
    return tuple(e.strip() for e in engines.split(",") if e.strip())


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------


@app.command()
def scrape(
    url: str = typer.Argument(..., help="URL to scrape."),
    engines: str | None = typer.Option(
        None,
        "--engines",
        help="Comma-separated engine override (e.g. 'jina,firecrawl').",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON to stdout."),
    output: Path | None = typer.Option(  # noqa: B008
        None, "--output", help="Write markdown to PATH instead of stdout."
    ),
) -> None:
    """Scrape a single URL and print the markdown (or full JSON with --json)."""
    opts = ScrapeOptions(engines=_engines_arg(engines))

    try:
        result: ScrapeResult = asyncio.run(scrapefold.scrape(url, opts))
    except AllEnginesFailed as exc:
        typer.echo(f"all engines failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

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
    max_pages: int = typer.Option(100, "--max-pages", help="Maximum pages to fetch (default 100)."),
    output: Path | None = typer.Option(  # noqa: B008
        None,
        "--output",
        help="Output .md file path for the stitched crawl result.",
    ),
    per_page_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--per-page-dir",
        help="Write each crawled page as <sha256(url)[:16]>.md into DIR.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON summary to stdout."),
) -> None:
    """Crawl a site → produce one stitched markdown file and/or per-page files."""
    opts = ScrapeOptions(max_pages=max_pages)

    try:
        crawl_result: Any = asyncio.run(scrapefold.crawl_site(url, opts=opts, output=output))
    except Exception as exc:
        typer.echo(f"crawl failed for {url}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Write per-page files if requested
    if per_page_dir is not None:
        per_page_dir.mkdir(parents=True, exist_ok=True)
        for page in crawl_result.pages:
            slug = hashlib.sha256(page.url.encode()).hexdigest()[:16]
            page_path = per_page_dir / f"{slug}.md"
            page_path.write_text(page.markdown)
            typer.echo(f"wrote {page_path} ({page.url})", err=True)

    stitched_path: Path | None = getattr(crawl_result, "stitched_path", None)

    if json_out:
        typer.echo(json.dumps({"output": str(stitched_path) if stitched_path else None}))
        return

    if stitched_path is not None:
        typer.echo(str(stitched_path))


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


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _doctor_report() -> dict[str, Any]:
    """Collect the health-check data for ``scrapefold doctor``."""
    import importlib.util
    import platform

    from scrapefold.engines import get_engine

    engines: dict[str, str] = {}
    for name in list_engine_names():
        try:
            get_engine(name)
            engines[name] = "ok"
        except Exception as exc:
            engines[name] = f"unavailable: {exc}"

    return {
        "version": scrapefold.__version__,
        "python": platform.python_version(),
        "mcp_extra": importlib.util.find_spec("mcp") is not None,
        "engines": engines,
    }


@app.command()
def doctor(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON report to stdout."),
) -> None:
    """Health check: version, MCP extra, and per-engine availability."""
    report = _doctor_report()

    if json_out:
        typer.echo(json.dumps(report))
        return

    typer.echo(f"scrapefold {report['version']} · python {report['python']}")
    if report["mcp_extra"]:
        typer.echo("mcp extra: installed (scrapefold-mcp ready)")
    else:
        typer.echo('mcp extra: NOT installed — pip install "scrapefold[mcp]" for the MCP server')

    ok = [name for name, status in report["engines"].items() if status == "ok"]
    missing = {n: s for n, s in report["engines"].items() if s != "ok"}
    typer.echo(f"engines: {len(ok)}/{len(report['engines'])} importable")
    for name, status in missing.items():
        typer.echo(f"  {name}: {status}")


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


@app.command()
def install(
    client: str = typer.Argument(
        "generic",
        help="Where to register the MCP server: claude | codex | cursor | vscode | generic.",
    ),
    print_only: bool = typer.Option(
        False, "--print-only", help="Show what would be done without executing."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit the generic mcpServers config JSON and exit."
    ),
) -> None:
    """One-click registration of the scrapefold MCP server into an AI client."""
    from scrapefold import install as install_mod

    if json_out:
        typer.echo(json.dumps(install_mod.mcp_config(), indent=2))
        return

    try:
        plan = install_mod.plan_install(client)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    try:
        typer.echo(install_mod.apply_plan(plan, print_only=print_only))
    except Exception as exc:
        typer.echo(f"install failed for {client}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def main() -> None:
    """Console-script entry point — referenced by pyproject `[project.scripts]`."""
    app()


if __name__ == "__main__":
    main()
