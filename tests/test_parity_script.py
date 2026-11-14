"""Offline checks for scripts/parity_ai_utils.py (the live parity runner)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "parity_ai_utils.py"
_spec = importlib.util.spec_from_file_location("parity_ai_utils", _PATH)
assert _spec and _spec.loader
parity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity)


def test_parses_ai_utils_python_repr() -> None:
    # ai-utils returns str(response.json()) for Scrapingdog's JSON endpoints.
    data = [{"fullName": "John Doe", "premium": True, "about": None}]
    assert parity.parse_structured(str(data)) == data
    assert parity.parse_structured('{"a": 1}') == {"a": 1}
    assert parity.parse_structured("# Plain markdown") is None


def test_compare_fields_reports_each_field() -> None:
    rows = parity.compare_fields(
        [{"name": "Acme", "size": 10, "old": 1}], [{"name": "Acme", "size": 11, "new": 2}]
    )
    assert {r["field"]: r["status"] for r in rows} == {
        "name": "same",
        "size": "differs",
        "old": "only ai-utils",
        "new": "only scrapefold",
    }


def test_compare_text_similarity() -> None:
    same = parity.compare_text("Hello\n\nworld", "Hello world")
    assert next(r for r in same if r["field"] == "similarity")["status"] == "same"
    diff = parity.compare_text("Hello world", "Something else entirely")
    assert next(r for r in diff if r["field"] == "similarity")["status"] == "differs"


def test_engine_mapping() -> None:
    engine, _ = parity.engine_and_opts(
        {"engine": "scrapingdog", "url": "https://www.linkedin.com/in/john-doe/"}
    )
    assert engine == "scrapingdog_dedicated"
    engine, opts = parity.engine_and_opts(
        {"engine": "firecrawl", "url": "https://example.com/", "whole_site": True, "limit": 7}
    )
    assert (engine, opts.max_pages, opts.max_depth, opts.main_content) == ("firecrawl", 7, 5, True)
