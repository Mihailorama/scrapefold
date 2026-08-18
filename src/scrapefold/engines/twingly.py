"""TwinglyEngine — paid REST API for blog search (twingly.com).

Twingly (https://app.twingly.com) indexes the global blogosphere and exposes
it through the Blog Search API: one GET endpoint that takes a search query in
Twingly's query language and returns matching blog posts as XML. This engine
is query-driven rather than URL-driven — the "target" is either a search
query or a blog URL that is turned into a posts-from-this-blog query.

Pure REST over ``httpx.AsyncClient`` — the official ``twingly-search`` SDK is
archived and synchronous, so it is not used.

Pinned API contract (verified 2026-08 against the official clients,
https://github.com/twingly/twingly-search-api-python):
  Method   : GET
  Base URL : https://api.twingly.com/blog/search/api/v3/search
  Auth     : apiKey=<api_key>  (query parameter)
  Request  : q=<query in the Twingly search language>
  Success  : XML root ``<twinglydata numberOfMatchesReturned=".." ``
             ``secondsElapsed=".." numberOfMatchesTotal=".." ``
             ``incompleteResult="..">`` with ``<post>`` children
  Failure  : XML root ``<error code=".."><message>..</message></error>``
  Billing  : commercial plans, per request. Trial keys via app.twingly.com.

Native parameter surface
------------------------

==============================  ===============================  ==============================
Twingly field                   Unified source                   Notes
==============================  ===============================  ==============================
``apiKey`` (query param)        ``TWINGLY_SEARCH_KEY`` / ctor    Required auth
``q`` (query param)             target / ``twingly_q`` / extras  Built by ``_build_query``
``lang:`` operator              ``opts.language``                Appended unless already in q
``<op>:<value>`` operators      ``opts.extra["twingly_<op>"]``   ``_`` -> ``-`` (``page_size`` ->
                                                                 ``page-size``)
==============================  ===============================  ==============================

Target -> query routing
-----------------------
=============================  ================================================
Target                          Query sent as ``q``
=============================  ================================================
``ai agents lang:en``           used verbatim (not a URL)
``https://blog.example.com/x``  ``blog.url:https://blog.example.com`` — recent
                                posts from that blog
``blog.example.com``            ``blog.url:https://blog.example.com``
=============================  ================================================

``opts.extra["twingly_q"]`` overrides the routed query entirely; other
``twingly_*`` extras are appended as query-language operators either way
(e.g. ``extra={"twingly_page_size": 30, "twingly_sort": "published"}`` adds
``page-size:30 sort:published``). Operators embedded in the query string
itself (``tspan:24h``, ``start-date:``, ``sort-order:`` …) pass through
untouched — the full language is documented at https://app.twingly.com.

Only the last ~12 months of the index are searchable through the API.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import markdown_to_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.twingly.com/blog/search/api/v3/search"
_COST_PER_CALL = 0.005  # commercial "contact us" pricing; coarse routing estimate

# XML child elements that hold lists, mapped to their item tag.
_LIST_TAGS = {"tags": "tag", "links": "link", "images": "image"}
_INT_TAGS = frozenset({"inlinksCount", "blogRank", "authority"})

# Schemeless targets that still look like a blog address ("blog.example.com",
# optionally with a path) rather than free-text search terms.
_BARE_HOST_RE = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")


def _looks_like_url(target: str) -> bool:
    return "://" in target or bool(_BARE_HOST_RE.fullmatch(target.strip()))


def _blog_url(target: str) -> str:
    """Normalize a target URL to ``scheme://host`` for a ``blog.url:`` query."""
    parsed = urlparse(target if "://" in target else f"https://{target.strip()}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_query(url: str, opts: ScrapeOptions) -> str:
    """Assemble the ``q`` search string from the target, extras, and language."""
    extra = strip_extra_prefix(opts.extra, "twingly_")
    base = extra.pop("q", None)
    if base is None:
        base = f"blog.url:{_blog_url(url)}" if _looks_like_url(url) else url
    parts = [str(base)]
    if opts.language and "lang:" not in str(base):
        parts.append(f"lang:{opts.language}")
    # Remaining twingly_* extras become query-language operators; underscores
    # map to the language's hyphens (page_size -> page-size). Sorted for
    # deterministic queries (and cache keys).
    for key in sorted(extra):
        parts.append(f"{key.replace('_', '-')}:{extra[key]}")
    return " ".join(parts)


def _parse_post(element: ET.Element) -> dict[str, Any]:
    post: dict[str, Any] = {}
    for child in element:
        if child.tag in _LIST_TAGS:
            item_tag = _LIST_TAGS[child.tag]
            post[child.tag] = [item.text or "" for item in child.findall(item_tag)]
        elif child.tag == "coordinates":
            latitude = child.findtext("latitude")
            longitude = child.findtext("longitude")
            if latitude is not None and longitude is not None:
                post[child.tag] = {"latitude": float(latitude), "longitude": float(longitude)}
            else:
                post[child.tag] = {}
        elif child.tag in _INT_TAGS:
            try:
                post[child.tag] = int(child.text or 0)
            except ValueError:
                post[child.tag] = child.text
        else:
            post[child.tag] = child.text
    return post


def _parse_response(body: bytes | str) -> dict[str, Any]:
    """Parse the Twingly XML envelope into a JSON-shaped dict.

    Raises ``ValueError`` on API errors (``<error>`` root), non-XML bodies,
    and unexpected root elements, so the base class wraps them as
    ``EngineError`` and the router can escalate.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"twingly returned a non-XML response: {exc}") from exc

    if root.tag == "error":
        code = root.attrib.get("code", "unknown")
        message = (root.findtext("message") or "").strip()
        raise ValueError(f"twingly error {code}: {message}")
    if root.tag != "twinglydata":
        raise ValueError(f"twingly returned unexpected root element {root.tag!r}")

    posts = [_parse_post(p) for p in root.findall("post")]
    return {
        "numberOfMatchesReturned": int(root.attrib.get("numberOfMatchesReturned", len(posts))),
        "numberOfMatchesTotal": int(root.attrib.get("numberOfMatchesTotal", len(posts))),
        "secondsElapsed": float(root.attrib.get("secondsElapsed", 0.0)),
        "incompleteResult": root.attrib.get("incompleteResult", "false").lower() == "true",
        "posts": posts,
    }


def _to_markdown(query: str, data: dict[str, Any]) -> str:
    """Render the parsed result as an LLM-ready markdown digest."""
    lines = [
        "# Twingly blog search",
        "",
        f"Query: `{query}`",
        f"Matches: {data['numberOfMatchesReturned']} returned "
        f"of {data['numberOfMatchesTotal']} total",
    ]
    if data["incompleteResult"]:
        lines.append("Note: the API flagged this result as incomplete.")
    for post in data["posts"]:
        title = post.get("title") or post.get("url") or "(untitled)"
        lines += ["", f"## {title}"]
        if post.get("url"):
            lines.append(f"- URL: {post['url']}")
        blog_name, blog_url = post.get("blogName"), post.get("blogUrl")
        if blog_name or blog_url:
            lines.append(
                f"- Blog: {blog_name or ''} {f'({blog_url})' if blog_url else ''}".rstrip()
            )
        if post.get("author"):
            lines.append(f"- Author: {post['author']}")
        if post.get("publishedAt"):
            lines.append(f"- Published: {post['publishedAt']}")
        if post.get("tags"):
            lines.append(f"- Tags: {', '.join(post['tags'])}")
        if post.get("text"):
            lines += ["", str(post["text"]).strip()]
    return "\n".join(lines)


class TwinglyEngine(ScrapeEngine):
    """Blog-search engine backed by the Twingly Blog Search API v3.

    Sends the routed query to the single ``/search`` endpoint, parses the XML
    envelope into ``ScrapeResult.json`` (``posts`` list + match counts), and
    renders a markdown digest of the matching posts into ``markdown``/``text``.

    API key from the constructor or ``TWINGLY_SEARCH_KEY`` (the official
    clients' convention). ``is_available()`` returns ``False`` when neither
    is set.
    """

    NAME = "twingly"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,  # vendor-side index; no direct fetch of the target
        requires_api_key=True,
        estimated_cost_usd=_COST_PER_CALL,
        billing_unit="call",
        proxy_type="none",
        site_classified=False,
        output_native_markdown=False,
        free_tier=False,  # commercial plans; trial keys on request
        avg_response_mb_estimate=0.5,  # XML payload
        bills_failed_attempts=True,
    )
    SUPPORTED_OPTIONS = frozenset({"language", "output_format", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None) -> None:
        super().__init__(api_key or os.getenv("TWINGLY_SEARCH_KEY"))

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        query = _build_query(url, opts)
        params = {"q": query, "apiKey": self.api_key or ""}

        logger.debug("twingly q=%r", query)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(_ENDPOINT, params=params)
        response.raise_for_status()

        data = _parse_response(response.content)
        markdown = _to_markdown(query, data)

        return ScrapeResult(
            url=url,
            text=markdown_to_text(markdown),
            markdown=markdown,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=self.CAPABILITIES.estimated_cost_usd,
            json=data,
            meta={
                "status_code": response.status_code,
                "twingly_query": query,
                "number_of_matches_total": data["numberOfMatchesTotal"],
                "incomplete_result": data["incompleteResult"],
            },
        )


__all__ = ["TwinglyEngine", "_build_query", "_parse_response"]
