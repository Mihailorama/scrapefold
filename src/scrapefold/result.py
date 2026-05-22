"""Unified result type returned by every scrape engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScrapeResult:
    """Outcome of a single scrape call.

    Every engine returns this exact shape. Each format slot is populated
    independently of the others:

    - ``text`` and ``markdown`` are always filled (post-converted from
      whichever native form the engine returned).
    - ``html`` is filled when the engine produced HTML; ``None`` otherwise.
    - ``json`` is filled by engines that return *structured* data natively
      (Firecrawl ``/extract``, AnySite endpoints, Apify actors). Free-form
      scraping engines leave it ``None``.

    Callers can pick the format they need:

        res.text        # always available
        res.markdown    # always available
        res.html        # may be None
        res.json        # may be None
        res.screenshot_b64  # may be None (only set when opts.take_screenshot)
    """

    url: str
    """The final URL after any redirects."""

    text: str
    """Clean plain text — no tags, no markdown. Always populated."""

    markdown: str
    """Markdown rendering — either engine-native or post-converted. Always populated."""

    html: str | None
    """Raw HTML when the engine produced HTML; ``None`` when markdown-only."""

    engine: str
    """Name of the engine that produced this result (e.g. ``"scrapling"``)."""

    elapsed_ms: int
    cost_usd: float = 0.0
    """Estimated USD cost of the call. ``0.0`` for free / local engines."""

    json: dict[str, Any] | list[Any] | None = None
    """Native structured data, if the engine returned any.

    Set by engines whose primary output is structured (Firecrawl ``/extract``,
    AnySite, Apify, vendor schema-extract endpoints). Free-form HTML/markdown
    engines leave this ``None``."""

    screenshot_b64: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    """Engine-specific extras (HTTP status, response headers, page title, …)."""

    failures: list[str] = field(default_factory=list)
    """Names of engines tried before this one succeeded, with reason."""

    @property
    def status_code(self) -> int | None:
        """HTTP status code stored under ``meta["status_code"]`` by the engine."""
        code = self.meta.get("status_code")
        return code if isinstance(code, int) else None

    def is_empty(self) -> bool:
        return (
            not self.text.strip()
            and not self.markdown.strip()
            and not self.html
            and self.json is None
        )

    def get_format(self, fmt: str) -> Any:
        """Return the result in the requested format, or ``None`` if unavailable.

        ``fmt`` must be one of ``"text"``, ``"markdown"``, ``"html"``, ``"json"``.
        """
        if fmt == "text":
            return self.text
        if fmt == "markdown":
            return self.markdown
        if fmt == "html":
            return self.html
        if fmt == "json":
            return self.json
        raise ValueError(f"unknown format {fmt!r}; expected one of text|markdown|html|json")


__all__ = ["ScrapeResult"]
