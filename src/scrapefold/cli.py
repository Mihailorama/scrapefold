"""scrapefold CLI — Typer entry point.

Scaffold (S1) — subcommands are stubs. Real implementations land in S9.

Usage examples (post-S9):

    scrapefold scrape https://example.com --engine firecrawl --language ru
    scrapefold crawl https://docs.example.com --max-pages 50 --output site.md
    scrapefold list-engines
    scrapefold inspect-opts firecrawl
"""

from __future__ import annotations

import sys

import typer

app = typer.Typer(
    name="scrapefold",
    help="Unified web scraping CLI. Single URL or whole-site → markdown.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def scrape(url: str, engine: str = "auto") -> None:
    """Scrape a single URL (stub — lands in S9)."""
    typer.echo(f"[stub] would scrape {url} with engine={engine}", err=True)
    sys.exit(2)


@app.command()
def crawl(url: str, max_pages: int = 50, output: str = "site.md") -> None:
    """Crawl a whole site → markdown (stub — lands in S9)."""
    typer.echo(f"[stub] would crawl {url} max_pages={max_pages} output={output}", err=True)
    sys.exit(2)


@app.command("list-engines")
def list_engines_cmd() -> None:
    """List registered engines and their availability (stub — lands in S9)."""
    from scrapefold.engines import list_engine_names

    names = list_engine_names()
    if not names:
        typer.echo("(no engines registered yet — scaffold only)")
    else:
        for name in names:
            typer.echo(name)


@app.command("inspect-opts")
def inspect_opts(engine: str) -> None:
    """Show which ScrapeOptions an engine supports (stub — lands in S9)."""
    typer.echo(f"[stub] would inspect opts of {engine}", err=True)
    sys.exit(2)


def main() -> None:
    """Console-script entry point for ``scrapefold``."""
    app()


if __name__ == "__main__":
    main()
