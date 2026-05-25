"""CrawlResult dataclass — returned by crawl_site() / crawl()."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scrapefold.result import ScrapeResult


@dataclass(slots=True, frozen=True)
class CrawlResult:
    """Return value from ``crawl_site()`` / ``crawl()``.

    Attributes
    ----------
    pages:
        Tuple of successfully scraped pages in discovery order.
    stitched_path:
        Path of the stitched markdown file written by the crawl.
        ``None`` if the caller explicitly opted out of writing (not yet
        exposed via API, reserved for future use).
    failures:
        Per-URL failure strings, one entry per URL that could not be
        scraped, formatted as ``"<url>:<ExcClass>:<short_msg>"``.
    """

    pages: tuple[ScrapeResult, ...]
    stitched_path: Path | None
    failures: tuple[str, ...] = ()


__all__ = ["CrawlResult"]
