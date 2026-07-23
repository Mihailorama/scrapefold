"""WaybackEngine — Internet Archive (Wayback Machine) fallback engine.

Free, keyless recovery for dead or blocked pages: looks up the closest
archived snapshot via the Wayback availability API, fetches it, and returns
it **honestly marked as archived** — ``ScrapeResult.json`` carries
``{"source": "archive.org", "snapshot_timestamp": ..., "snapshot_url": ...}``
so callers (and agents) always know the content is a snapshot, not live.

Native parameter surface:

| Native param | Unified option | Default | Notes |
|---|---|---|---|
| availability ``url`` | positional ``url`` | — | original page URL |
| availability ``timestamp`` | ``extra["wayback_timestamp"]`` | latest | ``YYYYMMDD[hhmmss]`` — pin closest-to-date snapshot |
| request headers | ``custom_headers`` | — | forwarded to both API and snapshot fetch |
| HTTP timeout | ``timeout_s`` | 60 | per request |

Intended use: explicit last-resort (``opts.engines=("wayback",)``) or as the
tail of a ladder for 404-class failures. Live-page engines should always be
preferred first.
"""

from __future__ import annotations

import logging

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_AVAILABILITY_API = "https://archive.org/wayback/available"


def _raw_snapshot_url(snapshot_url: str) -> str:
    """Rewrite a snapshot URL to the ``id_`` form (original bytes, no toolbar).

    ``https://web.archive.org/web/20260101000000/https://example.com`` →
    ``https://web.archive.org/web/20260101000000id_/https://example.com``
    """
    marker = "/web/"
    idx = snapshot_url.find(marker)
    if idx == -1:
        return snapshot_url
    rest = snapshot_url[idx + len(marker) :]
    timestamp, sep, target = rest.partition("/")
    if not sep or not timestamp.isdigit():
        return snapshot_url
    return f"{snapshot_url[: idx + len(marker)]}{timestamp}id_/{target}"


class WaybackEngine(ScrapeEngine):
    """Internet Archive snapshot engine — free, keyless, honest-by-design.

    Two HTTP calls: the availability API to locate the closest snapshot,
    then the snapshot itself (in raw ``id_`` form, without the Wayback
    toolbar). Raises :class:`EngineError` when no snapshot exists, so the
    router can report a clean failure instead of fake content.
    """

    NAME = "wayback"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=False,
        screenshot=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="none",
        free_tier=True,
        output_native_markdown=False,
        default_timeout_s=60,
        avg_response_mb_estimate=1.0,
    )
    SUPPORTED_OPTIONS = frozenset({"custom_headers", "timeout_s", "extra"})

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        headers = dict(opts.custom_headers or {})
        params: dict[str, str] = {"url": url}
        pinned = opts.extra.get("wayback_timestamp")
        if pinned:
            params["timestamp"] = str(pinned)

        async with httpx.AsyncClient(
            timeout=float(opts.timeout_s),
            follow_redirects=True,
        ) as client:
            avail = await client.get(_AVAILABILITY_API, params=params, headers=headers)
            avail.raise_for_status()
            snapshot = (avail.json().get("archived_snapshots") or {}).get("closest") or {}

            if not snapshot.get("available") or not snapshot.get("url"):
                raise EngineError(self.NAME, f"no archived snapshot for {url}")

            snapshot_url = str(snapshot["url"])
            page = await client.get(_raw_snapshot_url(snapshot_url), headers=headers)
            page.raise_for_status()

        html = page.text
        text, markdown = html_to_both(html, base_url=url)

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            json={
                "source": "archive.org",
                "snapshot_timestamp": snapshot.get("timestamp"),
                "snapshot_url": snapshot_url,
            },
            engine=self.NAME,
            elapsed_ms=0,
            meta={"status": page.status_code, "archived": True},
        )
