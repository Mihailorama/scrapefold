"""scrapefold MCP server entry point.

Exposes scrapefold to MCP clients (Claude Code, Claude Desktop, Cursor,
Codex, VS Code, …) over stdio. Requires the ``mcp`` optional extra:

    pip install "scrapefold[mcp]"

One-click registration into a client: ``scrapefold install claude`` (see
``scrapefold install --help`` for other clients).

Tools:
    - scrape_url    — single URL → markdown (auto engine escalation)
    - crawl_site    — whole site → stitched markdown
    - list_engines  — registered engine names
    - classify_url  — SiteClass the router would assign
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP

_INSTALL_HINT = (
    'scrapefold-mcp requires the "mcp" optional extra:\n\n    pip install "scrapefold[mcp]"\n'
)

_INSTRUCTIONS = (
    "scrapefold turns URLs into clean, LLM-ready markdown. "
    "Use scrape_url for a single page (the router auto-escalates from free "
    "local engines to stealth/paid ones only as far as the site forces it), "
    "crawl_site for a whole site, list_engines to see what is available, and "
    "classify_url to preview how the router would treat a URL."
)

_MAX_HTML_CHARS = 200_000


def _result_payload(result: Any) -> dict[str, Any]:
    """ScrapeResult → JSON-safe dict, dropping bulky/binary slots."""
    payload = asdict(result)
    payload.pop("screenshot_b64", None)
    html = payload.get("html")
    if html and len(html) > _MAX_HTML_CHARS:
        payload["html"] = None
    return payload


def build_server() -> FastMCP:
    """Construct the FastMCP server. Raises ImportError if ``mcp`` is missing."""
    from mcp.server.fastmcp import FastMCP

    import scrapefold
    from scrapefold import ScrapeOptions
    from scrapefold import classify_url as _classify
    from scrapefold.engines import list_engine_names

    server = FastMCP("scrapefold", instructions=_INSTRUCTIONS)

    @server.tool()
    async def scrape_url(
        url: str,
        engines: str | None = None,
        render_js: bool = True,
        stealth: bool = False,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """Scrape a single URL and return LLM-ready markdown.

        TIP: when you need one fact from a long page, pass focus="your
        question" — the response keeps only the relevant markdown blocks
        and costs a fraction of the tokens of the full page.

        Args:
            url: The URL to scrape.
            engines: Optional comma-separated engine override
                (e.g. "jina,firecrawl"). Default: router auto-selects.
            render_js: Render JavaScript before extracting content.
            stealth: Prefer anti-bot/stealth-capable engines.
            focus: Keyword query — return only markdown blocks relevant to
                it (BM25-ranked, with governing headings, in page order).
        """
        engine_tuple = (
            tuple(e.strip() for e in engines.split(",") if e.strip()) if engines else None
        )
        opts = ScrapeOptions(engines=engine_tuple, render_js=render_js, stealth=stealth)
        result = await scrapefold.scrape(url, opts)
        payload = _result_payload(result)
        if focus:
            from scrapefold.focus import focus_markdown

            payload["markdown"] = focus_markdown(payload["markdown"], focus)
            payload["text"] = ""
            payload["html"] = None
            payload["focus"] = focus
        return payload

    @server.tool()
    async def crawl_site(
        url: str,
        max_pages: int = 25,
    ) -> dict[str, Any]:
        """Crawl a whole site (sitemap → BFS) and return stitched markdown.

        Args:
            url: Root URL to crawl.
            max_pages: Maximum number of pages to fetch (default 25).
        """
        opts = ScrapeOptions(max_pages=max_pages)
        crawl = await scrapefold.crawl_site(url, opts=opts)
        stitched = crawl.stitched_path.read_text() if crawl.stitched_path is not None else None
        return {
            "url": url,
            "pages": [{"url": p.url, "engine": p.engine} for p in crawl.pages],
            "markdown": stitched,
            "failures": list(crawl.failures),
        }

    @server.tool()
    def list_engines() -> list[str]:
        """List every scraping engine registered in scrapefold."""
        return list_engine_names()

    @server.tool()
    def classify_url(url: str) -> dict[str, str]:
        """Return the SiteClass scrapefold's router would assign to a URL."""
        return {"url": url, "site_class": _classify(url)}

    return server


def main() -> None:
    """Console-script entry point for ``scrapefold-mcp`` (stdio transport)."""
    try:
        server = build_server()
    except ImportError:
        print(_INSTALL_HINT, file=sys.stderr)
        sys.exit(2)
    server.run()


if __name__ == "__main__":
    main()
