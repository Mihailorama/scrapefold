"""DuckDuckGoSearchEngine — keyless default via the DDG HTML endpoint.

This engine scrapes the ``https://duckduckgo.com/html/`` results page with a
stdlib :class:`html.parser.HTMLParser` (no bs4 dependency) and unwraps DDG's
``/l/?uddg=`` redirect links to the real target URLs.

Best-effort by nature: this is HTML scraping of a page DuckDuckGo can change at
any time. If the markup shifts and parsing yields nothing, ``_search`` returns
an empty list rather than raising, so the engine degrades quietly in a fusion
fan-out instead of failing the whole search.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from scrapefold.search.engines.base import SearchEngine
from scrapefold.search.options import SearchOptions
from scrapefold.search.types import SearchHit

logger = logging.getLogger(__name__)

_ENDPOINT = "https://duckduckgo.com/html/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _unwrap_ddg(href: str) -> str:
    """Resolve a DDG ``/l/?uddg=<encoded>`` redirect to the real target URL.

    Direct (non-redirect) hrefs are returned unchanged; protocol-relative hrefs
    get an ``https:`` scheme.
    """
    if not href:
        return ""
    parsed = urlparse(href)
    if parsed.path.startswith("/l/") and "duckduckgo.com" in parsed.netloc:
        uddg = parse_qs(parsed.query).get("uddg")
        return unquote(uddg[0]) if uddg else ""
    if not parsed.scheme and href.startswith("//"):
        return f"https:{href}"
    return href


class _DDGResultParser(HTMLParser):
    """Collects ``result__a`` anchors (title + href) and ``result__snippet`` text."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._current_href: str | None = None
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrd = dict(attrs)
        classes = (attrd.get("class") or "").split()
        if "result__a" in classes:
            self._in_title = True
            self._current_href = attrd.get("href")
            self._title_parts = []
        elif "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._in_title:
            self._in_title = False
            url = _unwrap_ddg(self._current_href or "")
            title = "".join(self._title_parts).strip()
            self._current_href = None
            if url:
                self.results.append({"url": url, "title": title, "snippet": ""})
        elif self._in_snippet:
            self._in_snippet = False
            snippet = "".join(self._snippet_parts).strip()
            if self.results and not self.results[-1]["snippet"]:
                self.results[-1]["snippet"] = snippet


class DuckDuckGoSearchEngine(SearchEngine):
    """Keyless DuckDuckGo HTML search engine (best-effort scraping)."""

    NAME = "duckduckgo"
    REQUIRES_API_KEY = False
    ESTIMATED_COST_USD = 0.0

    async def _search(self, query: str, opts: SearchOptions) -> list[SearchHit]:
        params = {"q": query}
        headers = {"User-Agent": _USER_AGENT}

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await client.get(_ENDPOINT, params=params, headers=headers)
            response.raise_for_status()
            html = response.text

        parser = _DDGResultParser()
        try:
            parser.feed(html)
        except Exception:  # pragma: no cover - defensive against odd markup
            logger.debug("duckduckgo html parse failed", exc_info=True)
            return []

        hits: list[SearchHit] = []
        for item in parser.results[: opts.count]:
            raw: dict[str, Any] = dict(item)
            hits.append(
                SearchHit(
                    url=item["url"],
                    title=item["title"],
                    snippet=item["snippet"],
                    raw=raw,
                )
            )
        return hits


__all__ = ["DuckDuckGoSearchEngine"]
