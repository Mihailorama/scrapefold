"""Universal Apify Actor engine for scrapefold.

Apify (https://apify.com) hosts thousands of community + first-party "actors"
— hosted scrapers — covering every major social platform and long-tail site.
``ApifyLinkedInEngine`` wraps one LinkedIn-specific actor; this engine
generalises that pattern to **any** actor, with site-classified defaults for
the common social platforms and a ``opts.extra["apify_actor_id"]`` escape hatch
to reach any of Apify's catalogue.

Cost: varies per actor; we expose a coarse ~$0.0015 / call estimate so the
router's budget ceilings still apply. Real cost is per-actor and per-result.

SDK: apify-client (install with ``pip install scrapefold[apify]``). The SDK is
lazy-imported inside ``_fetch`` so importing scrapefold does NOT require it.

URL -> default actor routing
----------------------------
The normal ``scrape(url)`` path maps a public social URL to a sensible public
actor. Override the actor per call via ``opts.extra["apify_actor_id"]``.

============================  =====================================
Target host                    Default actor id
============================  =====================================
instagram.com                  apify/instagram-scraper
tiktok.com                     clockworks/tiktok-scraper
twitter.com / x.com            apidojo/tweet-scraper
youtube.com / youtu.be         streamers/youtube-scraper
facebook.com / fb.com          apify/facebook-posts-scraper
reddit.com                     trudax/reddit-scraper
linkedin.com                   apimaestro/linkedin-profile-detail
============================  =====================================

A URL matching no known platform has no default actor: callers must supply
``opts.extra["apify_actor_id"]`` explicitly, otherwise a ``ValueError`` is
raised (wrapped as ``EngineError``) so the router escalates to another engine.

Actor input
-----------
Most actors accept ``startUrls`` as their primary input (Apify's most common
convention), so the default ``run_input`` is ``{"startUrls": [{"url": url}]}``.
Actors that expect a different input field (e.g. ``directUrls``) can be fed via
``apify_*`` extras: any ``opts.extra`` key prefixed with ``apify_`` (except the
routing key ``apify_actor_id``) is stripped of its prefix and merged into
``run_input`` — e.g. ``extra={"apify_directUrls": [url], "apify_resultsLimit": 50}``.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import normalize_social, platform_for_url

logger = logging.getLogger(__name__)

# Module-level reference to the SDK class, populated lazily on first use.
# Exposed at module scope so tests can monkeypatch it:
#   patch("scrapefold.engines.apify_actor.ApifyClientAsync", ...)
ApifyClientAsync: Any = None

_COST_PER_CALL = 0.0015

# Host substring -> default public actor id. Ordered by specificity is not
# needed since hosts are disjoint; a plain membership check suffices.
DEFAULT_ACTORS: dict[str, str] = {
    "instagram.com": "apify/instagram-scraper",
    "tiktok.com": "clockworks/tiktok-scraper",
    "twitter.com": "apidojo/tweet-scraper",
    "x.com": "apidojo/tweet-scraper",
    "youtube.com": "streamers/youtube-scraper",
    "youtu.be": "streamers/youtube-scraper",
    "facebook.com": "apify/facebook-posts-scraper",
    "fb.com": "apify/facebook-posts-scraper",
    "reddit.com": "trudax/reddit-scraper",
    "linkedin.com": "apimaestro/linkedin-profile-detail",
    # BEST-EFFORT defaults for Telegram / VK / Max — picked from the public
    # catalogue but NOT yet verified against a live run. A wrong slug would bill
    # the wrong actor, so confirm these (or override per-call via
    # opts.extra["apify_actor_id"]) before relying on them in production.
    "t.me": "73h/telegram-channel-scraper",  # verify
    "telegram.me": "73h/telegram-channel-scraper",  # verify
    "vk.com": "scrapearchitect/vk-scraper",  # verify
    "vk.ru": "scrapearchitect/vk-scraper",  # verify
    "max.ru": "scrapearchitect/max-messenger-scraper",  # verify
}


def _load_sdk() -> Any:
    """Return the ApifyClientAsync class, importing it on first call.

    Raises ImportError with an installation hint if apify-client is missing.
    """
    global ApifyClientAsync
    if ApifyClientAsync is not None:
        return ApifyClientAsync
    try:
        import apify_client as _ac  # lazy
    except ImportError as exc:
        raise ImportError(
            "apify-client is required for ApifyActorEngine. "
            "Install it with: pip install scrapefold[apify]"
        ) from exc
    ApifyClientAsync = _ac.ApifyClientAsync
    return ApifyClientAsync


def _host(url: str) -> str:
    """Return the lowercased host of ``url`` without a leading ``www.``."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _default_actor_for(url: str) -> str | None:
    """Return the default actor id for ``url``'s host, or ``None`` if unknown."""
    host = _host(url)
    for needle, actor in DEFAULT_ACTORS.items():
        if host == needle or host.endswith("." + needle):
            return actor
    return None


def _resolve_actor(url: str, opts: ScrapeOptions) -> str:
    """Resolve the actor id: explicit ``apify_actor_id`` extra wins, else default.

    Raises ``ValueError`` when neither an override nor a host default applies,
    so the router can escalate to another engine.
    """
    override = opts.extra.get("apify_actor_id") if opts.extra else None
    if override:
        return str(override)
    actor = _default_actor_for(url)
    if actor is None:
        raise ValueError(
            f"apify_actor has no default actor for URL {url!r}; "
            "set one via opts.extra['apify_actor_id']"
        )
    return actor


def _build_run_input(url: str, opts: ScrapeOptions) -> dict[str, Any]:
    """Build the ``run_input`` dict for the actor.

    Defaults to ``{"startUrls": [{"url": url}]}`` — the most common Apify input
    convention. Any ``apify_*`` extra (except the routing key ``apify_actor_id``)
    is stripped of its prefix and merged in, so callers can override the input
    field for actors using a different convention.
    """
    run_input: dict[str, Any] = {"startUrls": [{"url": url}]}

    passthrough = strip_extra_prefix(opts.extra, "apify_")
    passthrough.pop("actor_id", None)  # routing metadata, not actor input

    run_input.update(passthrough)
    return run_input


class ApifyActorEngine(ScrapeEngine):
    """Universal Apify-backed scraper.

    Resolves the target URL to a public default actor (or one forced via
    ``opts.extra["apify_actor_id"]``), runs it, and returns the dataset items
    as structured ``ScrapeResult.json`` — a single object when the run produced
    one item, the full list when it produced several (social feeds, comment
    threads).

    Requires ``APIFY_API_TOKEN`` in the environment (or pass ``api_key``
    directly). Install the optional extra: ``pip install scrapefold[apify]``.
    """

    NAME = "apify_actor"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        requires_api_key=True,
        output_native_markdown=False,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("APIFY_API_TOKEN"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Run the resolved actor and map its dataset items to a ScrapeResult."""
        client_cls = _load_sdk()
        client = client_cls(token=self.api_key)

        actor_id = _resolve_actor(url, opts)
        run_input = _build_run_input(url, opts)

        logger.debug("apify_actor actor=%s url=%s run_input=%s", actor_id, url, run_input)

        run = await client.actor(actor_id).call(run_input=run_input)

        if run is None:
            raise RuntimeError("Actor run returned None — actor may have timed out")

        dataset_id: str = run.default_dataset_id
        run_id: str = run.id

        page = await client.dataset(dataset_id).list_items()
        items: list[dict[str, Any]] = page.items

        if not items:
            raise RuntimeError(f"Actor run {run_id} produced no dataset items for URL: {url}")

        # Single-item actors (profile detail) -> the object; multi-item actors
        # (post feeds, comment threads) -> the whole list. Either way json is set.
        payload: Any = items[0] if len(items) == 1 else items
        text_out, markdown_out = json_to_scrape_text(payload)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=payload,
            social=normalize_social(payload, platform=platform_for_url(url)),
            meta={
                "actor_id": actor_id,
                "actor_run_id": run_id,
                "dataset_id": dataset_id,
                "item_count": len(items),
            },
        )


__all__ = ["DEFAULT_ACTORS", "ApifyActorEngine"]
