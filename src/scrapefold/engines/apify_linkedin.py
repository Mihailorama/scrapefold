"""Apify LinkedIn engine for scrapefold.

Uses the Apify platform to scrape LinkedIn profile pages via the
``apimaestro/linkedin-profile-detail`` actor (configurable via
``opts.extra["apify_actor_id"]``).

Cost: ~$0.0015 per call ($1.5 / 1 000 calls).
SDK: apify-client (install with ``pip install scrapefold[apify]``).

The SDK is lazy-imported inside ``_fetch`` so that importing scrapefold does
NOT require apify-client to be installed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.social import Kind, normalize_social

logger = logging.getLogger(__name__)

# Module-level reference to the SDK class, populated lazily on first use.
# Exposed at module scope so tests can monkeypatch it:
#   patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", ...)
ApifyClientAsync: Any = None

# Default Apify actor ID for LinkedIn profile scraping.
# Override per-call via opts.extra["apify_actor_id"].
DEFAULT_ACTOR_ID = "apimaestro/linkedin-profile-detail"

_COST_PER_CALL = 0.0015


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
            "apify-client is required for ApifyLinkedInEngine. "
            "Install it with: pip install scrapefold[apify]"
        ) from exc
    ApifyClientAsync = _ac.ApifyClientAsync
    return ApifyClientAsync


def _linkedin_kind(url: str) -> Kind:
    """LinkedIn URL -> normalized entity kind (posts/pulse are posts, else profile)."""
    lowered = url.lower()
    if "/posts/" in lowered or "/pulse/" in lowered:
        return "post"
    return "profile"


def _build_run_input(url: str, opts: ScrapeOptions) -> dict[str, Any]:
    """Build the run_input dict for the Apify actor.

    ``apimaestro/linkedin-profile-detail`` and most LinkedIn actors on Apify
    accept ``startUrls`` as the primary input (Apify's most common convention).
    Any ``opts.extra`` keys prefixed with ``apify_`` (excluding the special
    ``apify_actor_id``) are stripped of their prefix and merged in.
    """
    run_input: dict[str, Any] = {
        "startUrls": [{"url": url}],
    }

    # Forward apify_* extras (except apify_actor_id which is routing metadata)
    passthrough = strip_extra_prefix(opts.extra, "apify_")
    passthrough.pop("actor_id", None)  # already consumed above

    run_input.update(passthrough)
    return run_input


class ApifyLinkedInEngine(ScrapeEngine):
    """Apify-backed LinkedIn profile scraper.

    Calls the Apify actor ``apimaestro/linkedin-profile-detail`` (or any
    actor specified via ``opts.extra["apify_actor_id"]``) and returns the
    first dataset item as structured JSON.

    Requires ``APIFY_API_TOKEN`` in the environment (or pass ``api_key``
    directly to the constructor). Install the optional extra:
    ``pip install scrapefold[apify]``.

    The SDK (apify-client) is lazy-imported so that importing scrapefold
    doesn't require it to be installed.
    """

    NAME = "apify_linkedin"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        requires_api_key=True,
        output_native_markdown=False,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="residential",
        site_classified=True,
        avg_response_mb_estimate=0.5,  # JSON payload
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("APIFY_API_TOKEN"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Call the Apify actor and map the first dataset item to ScrapeResult."""
        client_cls = _load_sdk()
        client = client_cls(token=self.api_key)

        actor_id: str = (
            opts.extra.get("apify_actor_id", DEFAULT_ACTOR_ID) if opts.extra else DEFAULT_ACTOR_ID
        )
        run_input = _build_run_input(url, opts)

        logger.debug(
            "apify_linkedin actor=%s url=%s run_input=%s",
            actor_id,
            url,
            run_input,
        )

        run = await client.actor(actor_id).call(run_input=run_input)

        if run is None:
            raise RuntimeError("Actor run returned None — actor may have timed out")

        dataset_id: str = run.default_dataset_id
        run_id: str = run.id

        page = await client.dataset(dataset_id).list_items()
        items: list[dict[str, Any]] = page.items

        if not items:
            raise RuntimeError(f"Actor run {run_id} produced no dataset items for URL: {url}")

        profile: dict[str, Any] = items[0]
        text_out, markdown_out = json_to_scrape_text(profile)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=profile,
            social=normalize_social(profile, platform="linkedin", kind=_linkedin_kind(url)),
            meta={
                "actor_run_id": run_id,
                "dataset_id": dataset_id,
            },
        )


__all__ = ["DEFAULT_ACTOR_ID", "ApifyLinkedInEngine"]
