"""Source-span citation pinning for extracted data.

When an LLM (or a native structured engine) turns a page into JSON, the caller
loses the link between each extracted value and *where in the source* it came
from. An agent acting on the data cannot tell a value quoted verbatim from the
page apart from one the model paraphrased or invented.

This module rebuilds that link, with no new dependency and no LLM call. Given a
source text and an extracted JSON value, :func:`find_citations` walks every leaf
and locates it back in the source, returning a byte-range :class:`Span` per
value (or ``None`` when the value cannot be found — a grounding red flag).

    result = await scrape("https://example.com/product")
    data = await extract(result, schema={...}, llm=my_llm)
    cites = find_citations(result, data)
    for c in cites:
        print(c.path, "->", "grounded" if c.found else "NOT FOUND", c.span)

Matching is two-pass: an exact substring search first, then a whitespace- and
case-normalized search whose offsets are mapped back onto the *original* text
(so ``"Total:  99 USD"`` in the page still matches an extracted ``"Total: 99
USD"``). Purely lexical — it proves a string appears in the source, not that the
model interpreted it correctly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from scrapefold.result import ScrapeResult

MatchMethod = Literal["exact", "normalized", "none"]
"""How a value was located: verbatim, whitespace/case-normalized, or not at all."""

_DEFAULT_MIN_LEN = 2
"""Values whose string form is shorter than this are skipped (too noisy to pin)."""


@dataclass(frozen=True)
class Span:
    """A half-open ``[start, end)`` character range into the source text."""

    start: int
    end: int
    text: str
    """The exact source substring at ``source[start:end]``."""

    method: MatchMethod = "exact"

    def as_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text, "method": self.method}


@dataclass(frozen=True)
class Citation:
    """A single extracted value tied back to its location in the source."""

    path: str
    """Dotted / indexed path to the value, e.g. ``"price"`` or ``"items[0].name"``."""

    value: Any
    span: Span | None = None
    """The source range, or ``None`` when the value was not found in the source."""

    @property
    def found(self) -> bool:
        return self.span is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "value": self.value,
            "found": self.found,
            "span": self.span.as_dict() if self.span else None,
        }


# ---------------------------------------------------------------------------
# Normalized matching with offset mapping
# ---------------------------------------------------------------------------


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return a casefolded, whitespace-collapsed copy of *text* plus an index map.

    ``index_map[i]`` is the offset in the ORIGINAL ``text`` of the character that
    produced ``normalized[i]``. A run of whitespace collapses to a single space
    mapped to the first whitespace char of the run, so a match in the normalized
    string maps back onto a real range in the original.
    """
    out: list[str] = []
    index_map: list[int] = []
    prev_ws = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_ws:
                continue
            out.append(" ")
            index_map.append(i)
            prev_ws = True
        else:
            out.append(ch.casefold())
            index_map.append(i)
            prev_ws = False
    return "".join(out), index_map


def _find_span(source: str, norm_source: str, norm_map: list[int], value: str) -> Span | None:
    """Locate *value* in *source*, exact first then normalized. ``None`` if absent."""
    idx = source.find(value)
    if idx != -1:
        return Span(start=idx, end=idx + len(value), text=value, method="exact")

    norm_value = " ".join(value.split()).casefold()
    if not norm_value:
        return None
    nidx = norm_source.find(norm_value)
    if nidx == -1:
        return None
    # Map the normalized [nidx, nidx+len) range back to original offsets. The end
    # maps to the original position of the last matched normalized char, +1.
    start = norm_map[nidx]
    last = norm_map[nidx + len(norm_value) - 1]
    end = last + 1
    return Span(start=start, end=end, text=source[start:end], method="normalized")


# ---------------------------------------------------------------------------
# Walking the extracted value
# ---------------------------------------------------------------------------


def _citable_str(value: Any) -> str | None:
    """The string to search for a leaf value, or ``None`` if it isn't citable."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return None
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return None


def _walk(
    value: Any,
    path: str,
    source: str,
    norm_source: str,
    norm_map: list[int],
    min_len: int,
    out: list[Citation],
) -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            child = f"{path}.{key}" if path else str(key)
            _walk(sub, child, source, norm_source, norm_map, min_len, out)
        return
    if isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            _walk(sub, f"{path}[{i}]", source, norm_source, norm_map, min_len, out)
        return
    if value is None:
        return
    needle = _citable_str(value)
    if needle is None or len(needle.strip()) < min_len:
        return
    span = _find_span(source, norm_source, norm_map, needle)
    out.append(Citation(path=path, value=value, span=span))


def _source_text(source: ScrapeResult | str) -> str:
    if isinstance(source, ScrapeResult):
        return source.markdown or source.text
    return source


def find_citations(
    source: ScrapeResult | str,
    data: Any,
    *,
    min_len: int = _DEFAULT_MIN_LEN,
) -> list[Citation]:
    """Pin every leaf of *data* back to a span in *source*.

    Args:
        source: The scraped content the data was extracted from — a
            :class:`~scrapefold.result.ScrapeResult` (its ``markdown``, falling
            back to ``text``) or a raw string.
        data: The extracted value (typically ``result.json``): any nested mix of
            dict / list / str / number. Strings and numbers are searched;
            booleans and ``None`` are skipped.
        min_len: Skip leaves whose stripped string form is shorter than this
            (too short to pin meaningfully). Defaults to ``2``.

    Returns:
        One :class:`Citation` per citable leaf, in document order. A citation
        whose ``span`` is ``None`` (``found == False``) marks a value that does
        not appear in the source — the signal an agent uses to distrust it.
    """
    text = _source_text(source)
    norm_source, norm_map = _normalize_with_map(text)
    out: list[Citation] = []
    _walk(data, "", text, norm_source, norm_map, min_len, out)
    return out


@dataclass(frozen=True)
class CitationReport:
    """A :func:`find_citations` run plus a coverage summary."""

    citations: list[Citation] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.citations)

    @property
    def grounded(self) -> int:
        return sum(1 for c in self.citations if c.found)

    @property
    def coverage(self) -> float:
        """Fraction of citable leaves found in the source, in ``[0.0, 1.0]``."""
        return self.grounded / self.total if self.total else 1.0

    @property
    def ungrounded_paths(self) -> list[str]:
        """Paths whose value was NOT found — the values to distrust."""
        return [c.path for c in self.citations if not c.found]

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            "grounded": self.grounded,
            "total": self.total,
            "citations": [c.as_dict() for c in self.citations],
        }


def cite_result(result: ScrapeResult, *, min_len: int = _DEFAULT_MIN_LEN) -> CitationReport:
    """Cite ``result.json`` against ``result``'s own content.

    Convenience over :func:`find_citations` for the common case of grounding an
    engine's / :func:`~scrapefold.extract.extract_into`'s structured output back
    into the page it came from. Returns an empty report when ``result.json`` is
    ``None``.
    """
    if result.json is None:
        return CitationReport()
    return CitationReport(find_citations(result, result.json, min_len=min_len))


__all__ = [
    "Citation",
    "CitationReport",
    "MatchMethod",
    "Span",
    "cite_result",
    "find_citations",
]
