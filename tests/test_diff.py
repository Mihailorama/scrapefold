"""Tests for change detection (scrapefold.diff)."""

from __future__ import annotations

import json

import pytest

from scrapefold.diff import (
    ContentDiff,
    SnapshotStore,
    check_for_changes,
    diff_results,
    diff_text,
)
from scrapefold.result import ScrapeResult


def _result(text: str, *, url: str = "https://example.com", markdown: str | None = None):
    return ScrapeResult(
        url=url,
        text=text,
        markdown=markdown if markdown is not None else text,
        html=None,
        engine="stub",
        elapsed_ms=1,
    )


# --- diff_text / diff_results ---------------------------------------------


def test_identical_content_is_unchanged() -> None:
    d = diff_text("line one\nline two", "line one\nline two")
    assert d.changed is False
    assert d.similarity == 1.0
    assert d.added_lines == []
    assert d.removed_lines == []
    assert d.unified_diff == ""
    assert bool(d) is False


def test_whitespace_only_change_ignored_by_default() -> None:
    d = diff_text("Total:   99 USD", "Total: 99   USD")
    assert d.changed is False
    assert d.similarity == 1.0


def test_whitespace_change_detected_when_not_ignored() -> None:
    d = diff_text("a  b", "a b", ignore_whitespace=False)
    assert d.changed is True


def test_added_and_removed_lines_reported() -> None:
    d = diff_text("keep\nold line", "keep\nnew line")
    assert d.changed is True
    assert "new line" in d.added_lines
    assert "old line" in d.removed_lines
    assert 0.0 < d.similarity < 1.0
    assert "new line" in d.unified_diff
    assert bool(d) is True


def test_threshold_tolerates_small_change() -> None:
    old = "\n".join(f"line {i}" for i in range(100))
    new = old + "\none extra line"
    strict = diff_text(old, new, threshold=1.0)
    lenient = diff_text(old, new, threshold=0.9)
    assert strict.changed is True
    assert lenient.changed is False  # similarity stays above 0.9


def test_diff_results_on_markdown_field() -> None:
    old = _result("same text", markdown="# Old Heading")
    new = _result("same text", markdown="# New Heading")
    on_text = diff_results(old, new, on="text")
    on_md = diff_results(old, new, on="markdown")
    assert on_text.changed is False
    assert on_md.changed is True
    assert on_md.field_compared == "markdown"
    assert on_md.url == "https://example.com"


def test_content_diff_as_dict_json_serializable() -> None:
    d = diff_text("a", "b", url="https://example.com")
    json.dumps(d.as_dict())
    assert d.as_dict()["changed"] is True


# --- SnapshotStore ---------------------------------------------------------


async def test_snapshot_store_roundtrip(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    assert await store.load("https://example.com") is None

    original = _result("hello world", url="https://example.com")
    await store.save(original)
    loaded = await store.load("https://example.com")
    assert loaded is not None
    assert loaded.url == original.url
    assert loaded.text == "hello world"
    assert loaded.engine == "stub"


async def test_snapshot_store_overwrites_latest(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    await store.save(_result("v1", url="https://example.com"))
    await store.save(_result("v2", url="https://example.com"))
    loaded = await store.load("https://example.com")
    assert loaded is not None
    assert loaded.text == "v2"


async def test_snapshot_store_corrupt_file_is_a_miss(tmp_path) -> None:
    store = SnapshotStore(tmp_path)
    path = store._path_for("https://example.com")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json")
    assert await store.load("https://example.com") is None
    assert not path.exists()  # corrupt file removed


# --- check_for_changes (monitoring loop) -----------------------------------


async def test_check_for_changes_first_run_returns_none(tmp_path, monkeypatch) -> None:
    store = SnapshotStore(tmp_path)
    scrapes = iter([_result("initial", url="https://example.com")])

    async def fake_scrape(url, opts=None, pool=None):
        return next(scrapes)

    monkeypatch.setattr("scrapefold.scrape", fake_scrape)

    diff = await check_for_changes("https://example.com", store=store)
    assert diff is None  # baseline established, nothing to compare
    assert (await store.load("https://example.com")).text == "initial"


async def test_check_for_changes_detects_change_on_second_run(tmp_path, monkeypatch) -> None:
    store = SnapshotStore(tmp_path)
    scrapes = iter(
        [
            _result("original body", url="https://example.com"),
            _result("changed body", url="https://example.com"),
        ]
    )

    async def fake_scrape(url, opts=None, pool=None):
        return next(scrapes)

    monkeypatch.setattr("scrapefold.scrape", fake_scrape)

    first = await check_for_changes("https://example.com", store=store)
    assert first is None
    second = await check_for_changes("https://example.com", store=store)
    assert second is not None
    assert second.changed is True
    assert "changed body" in second.added_lines
    # The latest snapshot now reflects the new content.
    assert (await store.load("https://example.com")).text == "changed body"


async def test_check_for_changes_no_change_returns_unchanged_diff(tmp_path, monkeypatch) -> None:
    store = SnapshotStore(tmp_path)
    scrapes = iter(
        [
            _result("steady body", url="https://example.com"),
            _result("steady body", url="https://example.com"),
        ]
    )

    async def fake_scrape(url, opts=None, pool=None):
        return next(scrapes)

    monkeypatch.setattr("scrapefold.scrape", fake_scrape)

    await check_for_changes("https://example.com", store=store)
    second = await check_for_changes("https://example.com", store=store)
    assert isinstance(second, ContentDiff)
    assert second.changed is False


@pytest.mark.parametrize("on", ["text", "markdown"])
def test_diff_field_choices(on) -> None:
    old = _result("body a", markdown="md a")
    new = _result("body a", markdown="md a")
    assert diff_results(old, new, on=on).changed is False
