"""Shared HTML-to-text and HTML-to-markdown conversion for scrapefold engines.

Engines that return raw HTML (Firecrawl, ScrapingBee, Selenium, Scrapling, …)
call ``html_to_both()`` to populate both ``ScrapeResult.text`` and
``ScrapeResult.markdown`` before constructing the result.

Design decisions
----------------
- Uses **markdownify** (MIT) for markdown output; markdownify wraps
  BeautifulSoup4 and handles headings, lists, code blocks, tables, and links.
- Plain-text output is produced by stripping markdown markers from the
  markdownify result, giving consistent content across both outputs.
- Scripts and styles are removed with BeautifulSoup4 before conversion.
- Malformed HTML is parsed leniently via ``html.parser`` (stdlib) as the
  fallback inside BeautifulSoup4 — never raises on invalid markup.
- ``html2text`` (GPL) is intentionally NOT used.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify as _markdownify

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_MARKDOWN_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_LIST_MARKER_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+", re.MULTILINE)
_MARKDOWN_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MARKDOWN_TABLE_SEP_RE = re.compile(r"^\|[-| :]+\|$", re.MULTILINE)


def _clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove script and style tags in place, return the same soup."""
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return soup


def _parse_html(html: str) -> BeautifulSoup:
    """Parse HTML leniently. Never raises."""
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:  # pragma: no cover  # belt-and-suspenders
        _log.debug("html.parser failed; returning empty soup")
        return BeautifulSoup("", "html.parser")


def _resolve_url(href: str, base_url: str) -> str:
    """Resolve *href* against *base_url*. Returns href unchanged on error."""
    try:
        resolved = urljoin(base_url, href)
        # Sanity-check: must still be http(s)
        scheme = urlparse(resolved).scheme
        if scheme in ("http", "https"):
            return resolved
    except Exception:  # pragma: no cover
        pass
    return href


def _rewrite_links(soup: BeautifulSoup, base_url: str) -> None:
    """Resolve relative hrefs and srcs in the soup tree in place."""
    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "")
        tag["href"] = _resolve_url(str(href), base_url)
    for tag in soup.find_all("img", src=True):
        src = tag.get("src", "")
        tag["src"] = _resolve_url(str(src), base_url)


def _markdown_to_plain_text(md: str) -> str:
    """Strip markdown syntax from a markdown string to produce plain text.

    Handles:
    - Fenced code blocks (preserves content verbatim)
    - Images (dropped — img tags are not meaningful in plain text)
    - Links ([text](url) -> "text (url)")
    - Inline code (preserves content without backticks)
    - Heading markers (# ## ### …)
    - List markers (-, *, 1.)
    - Table separator rows
    - Horizontal rules
    """

    # Preserve fenced code block content (strip the fences themselves)
    def _unfence(m: re.Match[str]) -> str:
        inner = m.group(0)
        # Remove opening and closing ``` lines
        lines = inner.split("\n")
        # lines[0] is ```lang, lines[-1] is ``` — keep everything in between
        return "\n".join(lines[1:-1])

    text = _MARKDOWN_FENCE_RE.sub(_unfence, md)

    # Drop images entirely (no meaningful text representation)
    text = _MARKDOWN_IMAGE_RE.sub("", text)

    # Inline links: keep text, keep URL
    text = _MARKDOWN_LINK_RE.sub(
        lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(2) else m.group(1), text
    )

    # Inline code: strip backticks, keep content
    text = _MARKDOWN_INLINE_CODE_RE.sub(lambda m: m.group(0)[1:-1], text)

    # Heading markers
    text = _MARKDOWN_HEADING_RE.sub("", text)

    # List markers
    text = _MARKDOWN_LIST_MARKER_RE.sub(r"\1", text)

    # Table separator rows (|---|---|) — drop entirely
    text = _MARKDOWN_TABLE_SEP_RE.sub("", text)

    # Collapse intra-line whitespace
    lines = []
    for line in text.splitlines():
        line = _MULTI_SPACE_RE.sub(" ", line).strip()
        lines.append(line)

    # Re-join and collapse excess blank lines
    text = "\n".join(lines)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _render_markdown(html: str, *, base_url: str | None) -> str:
    """Single shared parse + markdownify pass; returns normalized markdown."""
    if not html or not html.strip():
        return ""
    soup = _clean_soup(_parse_html(html))
    if base_url:
        _rewrite_links(soup, base_url)
    md = _markdownify(
        str(soup),
        heading_style="ATX",
        bullets="-",
        code_language="",
    )
    return _MULTI_BLANK_RE.sub("\n\n", md).strip()


def _markdown_to_text(md: str, *, base_url: str | None) -> str:
    """Flatten markdown into plain text. Without ``base_url`` link URLs are dropped."""
    if not md:
        return ""
    if not base_url:
        md = _MARKDOWN_IMAGE_RE.sub("", md)
        md = _MARKDOWN_LINK_RE.sub(lambda m: m.group(1), md)
    return _markdown_to_plain_text(md)


def html_to_text(html: str, *, base_url: str | None = None) -> str:
    """Strip tags, collapse whitespace, preserve link URLs inline when base_url is set.

    Links are rendered as ``anchor text (resolved_url)`` when *base_url* is
    provided. Without *base_url*, only the anchor text is kept.

    Returns plain text. Empty or whitespace-only input returns ``""``.
    """
    return _markdown_to_text(_render_markdown(html, base_url=base_url), base_url=base_url)


def html_to_markdown(html: str, *, base_url: str | None = None) -> str:
    """Convert HTML to markdown.

    Preserves headings, lists, code blocks, tables, links, and images.
    Resolves relative URLs against *base_url* when provided.

    Returns markdown text. Empty or whitespace-only input returns ``""``.
    """
    return _render_markdown(html, base_url=base_url)


def html_to_both(html: str, *, base_url: str | None = None) -> tuple[str, str]:
    """Convenience: returns ``(text, markdown)`` from a single HTML string,
    sharing a single parse + markdownify pass.

    Engines use this to fill both ``ScrapeResult.text`` and
    ``ScrapeResult.markdown`` in one call.
    """
    md = _render_markdown(html, base_url=base_url)
    return _markdown_to_text(md, base_url=base_url), md


__all__ = ["html_to_both", "html_to_markdown", "html_to_text"]
