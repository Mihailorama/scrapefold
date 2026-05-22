"""scrapefold — unified interface for web scraping engines.

Public API (v0.1 — scaffold; engines land in later PRs):

    from scrapefold import scrape, crawl_site, ScrapeOptions, ScrapeResult, ScrapeEngine

    res = await scrape("https://example.com")
    res = await scrape(url, opts=ScrapeOptions(language="ru", stealth=True))
    md_path = await crawl_site("https://docs.example.com", opts=ScrapeOptions(max_pages=50))
"""

from __future__ import annotations

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.ladders import (
    AllEnginesFailed,
    BudgetExceeded,
    Policy,
    RaceStep,
    SequentialStep,
    SiteClass,
    WalkBudget,
    classify_url,
    get_ladder,
)
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

__version__ = "0.1.0a1"

__all__ = [
    "AllEnginesFailed",
    "BudgetExceeded",
    "EngineCapabilities",
    "EngineError",
    "Policy",
    "RaceStep",
    "ScrapeEngine",
    "ScrapeOptions",
    "ScrapeResult",
    "SequentialStep",
    "SiteClass",
    "WalkBudget",
    "__version__",
    "classify_url",
    "crawl_site",
    "get_ladder",
    "scrape",
]


async def scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
    """Single-URL scrape with engine auto-selection.

    Scaffold stub — full router lands in S7. Currently raises
    ``NotImplementedError`` since no engines are registered yet.
    """
    raise NotImplementedError(
        "scrape() requires at least one engine. Engines land in PRs S2-S6 and S11."
    )


async def crawl_site(url: str, opts: ScrapeOptions | None = None, **kwargs: object) -> str:
    """Whole-site crawl → single markdown file.

    Scaffold stub — crawler module lands in S8.
    """
    raise NotImplementedError("crawl_site() lands in S8 (crawler module).")
