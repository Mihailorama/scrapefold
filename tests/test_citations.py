"""Tests for source-span citation pinning (scrapefold.citations)."""

from __future__ import annotations

import json

from scrapefold.citations import (
    Citation,
    CitationReport,
    Span,
    cite_result,
    find_citations,
)
from scrapefold.result import ScrapeResult


def _result(text: str, *, markdown: str | None = None, data: object = None) -> ScrapeResult:
    return ScrapeResult(
        url="https://example.com",
        text=text,
        markdown=markdown if markdown is not None else text,
        html=None,
        engine="stub",
        elapsed_ms=1,
        json=data,
    )


def test_exact_match_offsets_and_method() -> None:
    source = "The price is 42 dollars for the Widget Pro."
    cites = find_citations(source, {"name": "Widget Pro"})
    assert len(cites) == 1
    c = cites[0]
    assert c.path == "name"
    assert c.found is True
    assert c.span is not None
    assert c.span.method == "exact"
    assert source[c.span.start : c.span.end] == "Widget Pro"
    assert c.span.text == "Widget Pro"


def test_normalized_match_maps_back_to_original_offsets() -> None:
    # Source has collapsed-doubled spaces and different case vs the extracted value.
    source = "Grand  Total:   99 USD\nnext line"
    cites = find_citations(source, {"total": "grand total: 99 usd"})
    c = cites[0]
    assert c.found is True
    assert c.span is not None
    assert c.span.method == "normalized"
    # The mapped span must recover the real (differently-spaced) source text.
    assert source[c.span.start : c.span.end] == "Grand  Total:   99 USD"


def test_value_not_in_source_is_ungrounded() -> None:
    cites = find_citations("nothing relevant here", {"name": "Hallucinated Corp"})
    assert cites[0].found is False
    assert cites[0].span is None


def test_nested_paths_and_indices() -> None:
    source = "Alice leads Sales. Bob leads Ops."
    data = {"team": [{"name": "Alice"}, {"name": "Bob"}], "dept": "Sales"}
    paths = {c.path: c for c in find_citations(source, data)}
    assert set(paths) == {"team[0].name", "team[1].name", "dept"}
    assert all(c.found for c in paths.values())


def test_numbers_cited_bool_and_none_skipped() -> None:
    source = "In stock: 42 units. Active."
    data = {"count": 42, "active": True, "note": None}
    cites = find_citations(source, data)
    paths = {c.path for c in cites}
    assert paths == {"count"}  # bool and None are not citable
    assert cites[0].found is True


def test_min_len_skips_short_values() -> None:
    source = "Rating a of 5"
    # Default min_len=2 skips the 1-char "a"; "55" (len 2) is attempted.
    cites = find_citations(source, {"grade": "a", "code": "55"})
    paths = {c.path for c in cites}
    assert paths == {"code"}


def test_scrape_result_prefers_markdown_then_text() -> None:
    r = _result(text="plain text only", markdown="# Heading\nWidget Pro here")
    cites = find_citations(r, {"name": "Widget Pro"})
    assert cites[0].found is True
    # Falls back to text when markdown is empty.
    r2 = _result(text="Widget Pro in text", markdown="")
    assert find_citations(r2, {"name": "Widget Pro"})[0].found is True


def test_cite_result_none_json_is_empty_full_coverage() -> None:
    report = cite_result(_result("anything", data=None))
    assert report.total == 0
    assert report.coverage == 1.0
    assert report.ungrounded_paths == []


def test_citation_report_coverage_and_ungrounded() -> None:
    source = "Only Alpha appears."
    report = CitationReport(find_citations(source, {"a": "Alpha", "b": "Beta"}))
    assert report.total == 2
    assert report.grounded == 1
    assert report.coverage == 0.5
    assert report.ungrounded_paths == ["b"]


def test_as_dict_is_json_serializable() -> None:
    report = cite_result(_result("Widget Pro sold here", data={"name": "Widget Pro"}))
    blob = report.as_dict()
    # Must round-trip through JSON (it is stored in ScrapeResult.meta, which the
    # disk cache serializes).
    reloaded = json.loads(json.dumps(blob))
    assert reloaded["coverage"] == 1.0
    assert reloaded["citations"][0]["path"] == "name"
    assert reloaded["citations"][0]["found"] is True
    assert reloaded["citations"][0]["span"]["method"] == "exact"


def test_span_and_citation_helpers() -> None:
    span = Span(start=0, end=3, text="abc", method="exact")
    assert span.as_dict() == {"start": 0, "end": 3, "text": "abc", "method": "exact"}
    c = Citation(path="x", value="abc", span=span)
    assert c.found is True
    assert Citation(path="y", value="z", span=None).found is False
