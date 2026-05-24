"""crawler — sitemap discovery + filtering + stitching.

Public entry point: ``async def crawl(root, opts, output) -> Path``.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import scrapefold.crawler.sitemap as sitemap
from scrapefold.crawler.stitcher import write_stitched
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)


async def crawl(
    root: str,
    opts: ScrapeOptions | None = None,
    output: Path | str | None = None,
) -> Path:
    """Walk a site → produce one stitched markdown file.

    Discovery: sitemap → robots → BFS (see ``crawler.sitemap``).
    Per-URL fetch delegates to ``scrapefold.scrape``.
    Output: ``crawler.stitcher.write_stitched``.
    """
    from scrapefold import scrape  # local import — avoids circular

    opts = opts or ScrapeOptions()
    max_pages = opts.max_pages if opts.max_pages is not None else 100
    if max_pages <= 0:
        if output is None:
            output = Path(tempfile.gettempdir()) / "scrapefold-crawl.md"
        return write_stitched([], Path(output))

    urls = await sitemap.discover_urls(
        root,
        max_urls=max_pages,
        max_depth=opts.max_depth,
        follow_subdomains=opts.follow_subdomains,
    )
    logger.info("crawler: discovered %d urls from %s", len(urls), root)

    if not urls:
        urls = [root]  # at least try the root

    results: list[ScrapeResult] = []
    for url in urls:
        try:
            results.append(await scrape(url, opts))
        except Exception as exc:  # broad — per-URL failures must not abort the crawl
            logger.warning("crawler: scrape failed url=%s err=%s", url, exc)

    if output is None:
        output = Path(tempfile.gettempdir()) / "scrapefold-crawl.md"
    output = Path(output)

    return write_stitched(results, output)


__all__ = ["crawl"]
