"""Engine registry.

Engines are imported lazily so that an optional vendor SDK missing on the
machine (e.g. ``selenium`` not installed) does not break import of
``scrapefold`` itself. ``get_engine(name)`` returns a class on demand.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scrapefold.engines.base import ScrapeEngine

# Lazy registry: name -> import-and-return-class function.
# Each lambda imports the engine module on first call so missing extras
# only error out when that engine is actually requested.
_REGISTRY: dict[str, Callable[[], type[ScrapeEngine]]] = {
    "requests": lambda: (
        __import__("scrapefold.engines.requests", fromlist=["RequestsEngine"]).RequestsEngine
    ),
    "firecrawl": lambda: (
        __import__("scrapefold.engines.firecrawl", fromlist=["FirecrawlEngine"]).FirecrawlEngine
    ),
    "scrapingbee": lambda: (
        __import__(
            "scrapefold.engines.scrapingbee", fromlist=["ScrapingbeeEngine"]
        ).ScrapingbeeEngine
    ),
    "scrapingdog": lambda: (
        __import__(
            "scrapefold.engines.scrapingdog", fromlist=["ScrapingdogEngine"]
        ).ScrapingdogEngine
    ),
    "scraperapi": lambda: (
        __import__("scrapefold.engines.scraperapi", fromlist=["ScraperApiEngine"]).ScraperApiEngine
    ),
    "exa": lambda: __import__("scrapefold.engines.exa", fromlist=["ExaEngine"]).ExaEngine,
    "pixelrag": lambda: (
        __import__("scrapefold.engines.pixelrag", fromlist=["PixelRagEngine"]).PixelRagEngine
    ),
    "jina": lambda: __import__("scrapefold.engines.jina", fromlist=["JinaEngine"]).JinaEngine,
    "oxylabs": lambda: (
        __import__("scrapefold.engines.oxylabs", fromlist=["OxylabsEngine"]).OxylabsEngine
    ),
    "apify_linkedin": lambda: (
        __import__(
            "scrapefold.engines.apify_linkedin", fromlist=["ApifyLinkedInEngine"]
        ).ApifyLinkedInEngine
    ),
    "apify_actor": lambda: (
        __import__("scrapefold.engines.apify_actor", fromlist=["ApifyActorEngine"]).ApifyActorEngine
    ),
    "anysite": lambda: (
        __import__("scrapefold.engines.anysite", fromlist=["AnySiteEngine"]).AnySiteEngine
    ),
    "scrapecreators": lambda: (
        __import__(
            "scrapefold.engines.scrapecreators", fromlist=["ScrapeCreatorsEngine"]
        ).ScrapeCreatorsEngine
    ),
    "socialcrawl": lambda: (
        __import__(
            "scrapefold.engines.socialcrawl", fromlist=["SocialCrawlEngine"]
        ).SocialCrawlEngine
    ),
    "outscraper": lambda: (
        __import__("scrapefold.engines.outscraper", fromlist=["OutscraperEngine"]).OutscraperEngine
    ),
    "scrapling_stealth": lambda: (
        __import__(
            "scrapefold.engines.scrapling_stealth", fromlist=["ScraplingStealthEngine"]
        ).ScraplingStealthEngine
    ),
    "scrapling_fast": lambda: (
        __import__(
            "scrapefold.engines.scrapling_fast", fromlist=["ScraplingFastEngine"]
        ).ScraplingFastEngine
    ),
    "cloudflare": lambda: (
        __import__("scrapefold.engines.cloudflare", fromlist=["CloudflareEngine"]).CloudflareEngine
    ),
    "crawl4ai": lambda: (
        __import__("scrapefold.engines.crawl4ai", fromlist=["Crawl4AIEngine"]).Crawl4AIEngine
    ),
    "cloakbrowser": lambda: (
        __import__(
            "scrapefold.engines.cloakbrowser", fromlist=["CloakBrowserEngine"]
        ).CloakBrowserEngine
    ),
    "selenium": lambda: (
        __import__("scrapefold.engines.selenium", fromlist=["SeleniumEngine"]).SeleniumEngine
    ),
    "telegram": lambda: (
        __import__("scrapefold.engines.telegram", fromlist=["TelegramEngine"]).TelegramEngine
    ),
    "tgstat": lambda: (
        __import__("scrapefold.engines.tgstat", fromlist=["TGStatEngine"]).TGStatEngine
    ),
    "telemetr": lambda: (
        __import__("scrapefold.engines.telemetr", fromlist=["TelemetrEngine"]).TelemetrEngine
    ),
    "labelup": lambda: (
        __import__("scrapefold.engines.labelup", fromlist=["LabelUpEngine"]).LabelUpEngine
    ),
}


# User-facing aliases for multi-mode engines so ``opts.engines=["scrapling"]``
# resolves to the canonical ``scrapling_stealth``, while
# ``WalkBudget.engines_tried`` stays keyed by unambiguous canonical names.
ENGINE_ALIASES: dict[str, str] = {}


def register(name: str, loader: Callable[[], type[ScrapeEngine]]) -> None:
    _REGISTRY[name] = loader


def register_alias(alias: str, canonical: str) -> None:
    """Register ``alias`` as a user-facing name for canonical engine ``canonical``."""
    ENGINE_ALIASES[alias] = canonical


def resolve_alias(name: str) -> str:
    """Return the canonical engine name for ``name``, or ``name`` if no alias."""
    return ENGINE_ALIASES.get(name, name)


def get_engine(name: str) -> type[ScrapeEngine]:
    """Return the engine class for ``name`` (alias-resolved). Raises KeyError if unknown."""
    canonical = resolve_alias(name)
    try:
        loader = _REGISTRY[canonical]
    except KeyError as exc:
        raise KeyError(
            f"unknown engine: {name!r} (resolved to {canonical!r}). known: {sorted(_REGISTRY)}"
        ) from exc
    return loader()


def list_engine_names() -> list[str]:
    return sorted(_REGISTRY)


# Register user-facing aliases for multi-mode engines.
# "scrapling" resolves to "scrapling_stealth" (default / most capable mode).
register_alias("scrapling", "scrapling_stealth")
# "apify" resolves to the universal actor engine (apify_linkedin stays explicit).
register_alias("apify", "apify_actor")


__all__ = [
    "ENGINE_ALIASES",
    "get_engine",
    "list_engine_names",
    "register",
    "register_alias",
    "resolve_alias",
]
