"""LinkedInPublicEngine — keyless read of LinkedIn's public pages (SCRAPEFOLD-32).

LinkedIn serves logged-out visitors and search crawlers a public version of
person profiles (``/in/``), company pages (``/company/``, ``/showcase/``,
``/school/``) and job postings (``/jobs/view/``). Those pages carry schema.org
JSON-LD: a ``Person``, ``Organization`` or ``JobPosting`` object, plus recent
posts as ``Article`` / ``DiscussionForumPosting`` / ``SocialMediaPosting``.
This engine fetches the page and returns that JSON-LD as ``ScrapeResult.json``.

No API key, no account, no cookies. LinkedIn answers a crawler User-Agent more
often than a browser one, so the engine tries both. From a datacenter IP it
can still be throttled (HTTP 999 / auth wall); then it raises ``EngineError``
and the ladder moves on to the next step (keenable, then paid providers).

``json`` shape::

    {"type": "Person" | "Organization" | "JobPosting",
     "entity": {...the schema.org object...},
     "posts": [{...}, ...],          # up to 10, profile and company pages only
     "title": og:title, "description": og:description}
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult
from scrapefold.social import normalize_social

logger = logging.getLogger(__name__)

_USER_AGENTS = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
_MAIN_TYPES = ("Person", "Organization", "JobPosting")
_POST_TYPES = ("Article", "DiscussionForumPosting", "SocialMediaPosting")
_MAX_POSTS = 10
_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_OG_RE = re.compile(r'<meta[^>]+property="og:(title|description)"[^>]+content="([^"]*)"', re.I)


def _ld_objects(page: str) -> list[dict[str, Any]]:
    """All JSON-LD objects on the page, ``@graph`` flattened."""
    out: list[dict[str, Any]] = []
    for raw in _LD_RE.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        out.extend(x for x in items if isinstance(x, dict))
    return out


def _type(obj: dict[str, Any]) -> str:
    t = obj.get("@type")
    return t[0] if isinstance(t, list) and t else str(t or "")


def parse_public_page(page: str) -> dict[str, Any] | None:
    """Return the structured payload, or ``None`` when the page has no main entity."""
    objs = _ld_objects(page)
    entity = next((o for o in objs if _type(o) in _MAIN_TYPES), None)
    if entity is None:
        return None
    og = {k: html_lib.unescape(v) for k, v in _OG_RE.findall(page)}
    return {
        "type": _type(entity),
        "entity": entity,
        "posts": [o for o in objs if _type(o) in _POST_TYPES][:_MAX_POSTS],
        "title": og.get("title"),
        "description": og.get("description"),
    }


class LinkedInPublicEngine(ScrapeEngine):
    """Keyless LinkedIn profile / company / job reader over public JSON-LD."""

    NAME = "linkedin_public"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        requires_api_key=False,
        estimated_cost_usd=0.0,
        free_tier=True,
        site_classified=True,
        output_native_markdown=False,
        avg_response_mb_estimate=0.4,
    )
    SUPPORTED_OPTIONS = frozenset({"timeout_s"})

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        last = "no attempt"
        async with httpx.AsyncClient(
            timeout=float(opts.timeout_s), follow_redirects=True
        ) as client:
            for ua in _USER_AGENTS:
                response = await client.get(
                    url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}
                )
                payload = parse_public_page(response.text) if response.status_code == 200 else None
                if payload is not None:
                    break
                last = f"HTTP {response.status_code} at {response.url}"
                logger.debug("linkedin_public: no entity (%s)", last)
            else:
                raise EngineError(self.NAME, f"no public LinkedIn data ({last})")

        text_out, markdown_out = json_to_scrape_text(payload)
        social = (
            normalize_social(payload["entity"], platform="linkedin", kind="profile")
            if payload["type"] in ("Person", "Organization")
            else None
        )
        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=0.0,
            json=payload,
            social=social,
            meta={"status_code": response.status_code, "ld_type": payload["type"]},
        )


__all__ = ["LinkedInPublicEngine", "parse_public_page"]
