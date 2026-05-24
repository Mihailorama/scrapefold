"""Tests for crawler.stitcher — multi-page markdown writer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from scrapefold.crawler.stitcher import write_stitched
from scrapefold.result import ScrapeResult


def _result(url: str, md: str, engine: str = "requests") -> ScrapeResult:
    return ScrapeResult(url=url, text=md, markdown=md, html=None, engine=engine, elapsed_ms=42)


def test_writes_single_file_with_two_sections(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    results = [
        _result("https://example.com/a", "# A\n\nAlpha body."),
        _result("https://example.com/b", "# B\n\nBeta body."),
    ]
    write_stitched(results, out)

    text = out.read_text()
    # Two front-matter blocks
    assert text.count("---\n") >= 4  # 2 open + 2 close
    assert "# A" in text
    assert "# B" in text


def test_front_matter_is_valid_yaml(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    write_stitched(
        [_result("https://example.com/a", "# A\n\nAlpha.")],
        out,
    )
    text = out.read_text()

    # Pull the first front-matter block
    start = text.index("---\n") + len("---\n")
    end = text.index("\n---\n", start)
    meta = yaml.safe_load(text[start:end])

    assert meta["url"] == "https://example.com/a"
    assert meta["engine"] == "requests"
    assert meta["elapsed_ms"] == 42
    assert isinstance(meta["fetched_at"], str)
    # ISO-8601 round-trip
    datetime.fromisoformat(meta["fetched_at"])


def test_empty_results_writes_empty_file(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    write_stitched([], out)
    assert out.read_text() == ""


def test_creates_parent_directories(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "deep" / "site.md"
    write_stitched(
        [_result("https://example.com/", "# Root")],
        out,
    )
    assert out.exists()


def test_returns_path(tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    returned = write_stitched(
        [_result("https://example.com/", "# Root")],
        out,
    )
    assert returned == out
