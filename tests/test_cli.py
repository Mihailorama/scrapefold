"""Tests for the scrapefold Typer CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from scrapefold.cli import app
from scrapefold.crawler.result import CrawlResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_scrape(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from scrapefold.options import ScrapeOptions
    from scrapefold.result import ScrapeResult

    captured: dict[str, Any] = {"called_with": None}

    async def _fake_scrape(url: str, opts: ScrapeOptions | None = None) -> ScrapeResult:
        captured["called_with"] = {
            "url": url,
            "engines": list(opts.engines) if opts and opts.engines else None,
        }
        return ScrapeResult(
            url=url,
            text="hello text",
            markdown="# hello\n\nbody",
            html=None,
            engine="stub",
            elapsed_ms=12,
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)
    return captured


# ---------------------------------------------------------------------------
# 1. `scrapefold scrape <url>` prints markdown to stdout, exit 0
# ---------------------------------------------------------------------------


def test_scrape_command_prints_markdown(runner: CliRunner, stub_scrape: dict[str, Any]) -> None:
    result = runner.invoke(app, ["scrape", "https://example.com/"])
    assert result.exit_code == 0
    assert "# hello" in result.stdout
    assert stub_scrape["called_with"]["url"] == "https://example.com/"


# ---------------------------------------------------------------------------
# 2. `scrapefold scrape <url> --json` prints JSON, exit 0
# ---------------------------------------------------------------------------


def test_scrape_command_json_output(runner: CliRunner, stub_scrape: dict[str, Any]) -> None:
    result = runner.invoke(app, ["scrape", "https://example.com/", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["url"] == "https://example.com/"
    assert data["markdown"] == "# hello\n\nbody"
    assert data["engine"] == "stub"


# ---------------------------------------------------------------------------
# 3. `--engines` accepts comma-separated and forwards to opts.engines
# ---------------------------------------------------------------------------


def test_scrape_command_engines_override(runner: CliRunner, stub_scrape: dict[str, Any]) -> None:
    runner.invoke(app, ["scrape", "https://example.com/", "--engines", "jina,firecrawl"])
    assert stub_scrape["called_with"]["engines"] == ["jina", "firecrawl"]


# ---------------------------------------------------------------------------
# 4. `--output PATH` writes markdown to file
# ---------------------------------------------------------------------------


def test_scrape_command_output_to_file(
    runner: CliRunner, stub_scrape: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "scrape.md"
    result = runner.invoke(app, ["scrape", "https://example.com/", "--output", str(out)])
    assert result.exit_code == 0
    assert out.read_text() == "# hello\n\nbody"


# ---------------------------------------------------------------------------
# 5. `scrapefold scrape` propagates AllEnginesFailed → exit 1
# ---------------------------------------------------------------------------


def test_scrape_command_all_engines_failed_exit_1(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scrapefold import AllEnginesFailed

    async def _raise(url, opts=None):
        raise AllEnginesFailed(url, ["requests: 403", "jina: timeout"])

    monkeypatch.setattr("scrapefold.scrape", _raise)

    result = runner.invoke(app, ["scrape", "https://example.com/"])
    assert result.exit_code == 1
    assert "all engines failed" in result.output.lower()


# ---------------------------------------------------------------------------
# 6. `scrapefold list-engines` prints registered engine names
# ---------------------------------------------------------------------------


def test_list_engines_command(runner: CliRunner) -> None:
    result = runner.invoke(app, ["list-engines"])
    assert result.exit_code == 0
    # At least one known engine should be in the output
    assert "requests" in result.stdout
    assert "firecrawl" in result.stdout


def test_list_engines_json_output(runner: CliRunner) -> None:
    result = runner.invoke(app, ["list-engines", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert "requests" in data


# ---------------------------------------------------------------------------
# 7. `scrapefold classify <url>` returns the SiteClass
# ---------------------------------------------------------------------------


def test_classify_command_known_url(runner: CliRunner) -> None:
    result = runner.invoke(app, ["classify", "https://www.linkedin.com/in/someone"])
    assert result.exit_code == 0
    assert "linkedin" in result.stdout.lower()


def test_classify_command_json(runner: CliRunner) -> None:
    result = runner.invoke(app, ["classify", "https://www.linkedin.com/in/someone", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "url" in data
    assert "site_class" in data


# ---------------------------------------------------------------------------
# 8. `scrapefold crawl <url> --max-pages 3` calls crawl_site (CrawlResult API)
# ---------------------------------------------------------------------------


def test_crawl_command(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = tmp_path / "site.md"
    out.write_text("crawled content")

    crawl_result = CrawlResult(
        pages=(),
        stitched_path=out,
        failures=(),
    )

    mock_crawl = AsyncMock(return_value=crawl_result)
    monkeypatch.setattr("scrapefold.crawl_site", mock_crawl)

    result = runner.invoke(
        app,
        ["crawl", "https://example.com/", "--max-pages", "3", "--output", str(out)],
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 9. `scrapefold --version` works
# ---------------------------------------------------------------------------


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    import scrapefold

    assert scrapefold.__version__ in result.stdout


# ---------------------------------------------------------------------------
# 10. `scrapefold --help` lists all four subcommands
# ---------------------------------------------------------------------------


def test_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scrape" in result.stdout
    assert "crawl" in result.stdout
    assert "list-engines" in result.stdout
    assert "classify" in result.stdout


# ---------------------------------------------------------------------------
# 11. `--per-page-dir` writes one file per page
# ---------------------------------------------------------------------------


def test_crawl_per_page_dir_writes_one_file_per_page(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scrapefold.result import ScrapeResult

    pages = (
        ScrapeResult(
            url="https://example.com/a",
            text="A",
            markdown="# A",
            html=None,
            engine="stub",
            elapsed_ms=1,
        ),
        ScrapeResult(
            url="https://example.com/b",
            text="B",
            markdown="# B",
            html=None,
            engine="stub",
            elapsed_ms=1,
        ),
    )
    crawl_result = CrawlResult(pages=pages, stitched_path=None)
    mock_crawl = AsyncMock(return_value=crawl_result)
    monkeypatch.setattr("scrapefold.crawl_site", mock_crawl)

    per_page_dir = tmp_path / "pages"
    result = runner.invoke(
        app,
        ["crawl", "https://example.com/", "--per-page-dir", str(per_page_dir)],
    )
    assert result.exit_code == 0
    written = list(per_page_dir.glob("*.md"))
    assert len(written) == 2


def test_crawl_per_page_dir_uses_sha256_url_prefix_filename(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scrapefold.result import ScrapeResult

    url = "https://example.com/page"
    expected_prefix = hashlib.sha256(url.encode()).hexdigest()[:16]

    pages = (
        ScrapeResult(
            url=url, text="body", markdown="# body", html=None, engine="stub", elapsed_ms=1
        ),
    )
    crawl_result = CrawlResult(pages=pages, stitched_path=None)
    mock_crawl = AsyncMock(return_value=crawl_result)
    monkeypatch.setattr("scrapefold.crawl_site", mock_crawl)

    per_page_dir = tmp_path / "pages"
    runner.invoke(
        app,
        ["crawl", "https://example.com/", "--per-page-dir", str(per_page_dir)],
    )
    written = list(per_page_dir.glob("*.md"))
    assert len(written) == 1
    assert written[0].name == f"{expected_prefix}.md"


def test_crawl_per_page_dir_prints_each_path_to_stderr(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scrapefold.result import ScrapeResult

    pages = (
        ScrapeResult(
            url="https://example.com/x",
            text="X",
            markdown="# X",
            html=None,
            engine="stub",
            elapsed_ms=1,
        ),
    )
    crawl_result = CrawlResult(pages=pages, stitched_path=None)
    mock_crawl = AsyncMock(return_value=crawl_result)
    monkeypatch.setattr("scrapefold.crawl_site", mock_crawl)

    per_page_dir = tmp_path / "pages"
    # Typer's CliRunner mixes stdout+stderr into result.output by default
    result = runner.invoke(
        app,
        ["crawl", "https://example.com/", "--per-page-dir", str(per_page_dir)],
    )
    assert result.exit_code == 0
    assert "wrote" in result.output
    assert "https://example.com/x" in result.output


def test_crawl_per_page_dir_works_alongside_stitched_output(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scrapefold.result import ScrapeResult

    stitched = tmp_path / "site.md"
    stitched.write_text("stitched content")

    pages = (
        ScrapeResult(
            url="https://example.com/p",
            text="P",
            markdown="# P",
            html=None,
            engine="stub",
            elapsed_ms=1,
        ),
    )
    crawl_result = CrawlResult(pages=pages, stitched_path=stitched)
    mock_crawl = AsyncMock(return_value=crawl_result)
    monkeypatch.setattr("scrapefold.crawl_site", mock_crawl)

    per_page_dir = tmp_path / "pages"
    result = runner.invoke(
        app,
        [
            "crawl",
            "https://example.com/",
            "--output",
            str(stitched),
            "--per-page-dir",
            str(per_page_dir),
        ],
    )
    assert result.exit_code == 0
    # Stitched file untouched
    assert stitched.read_text() == "stitched content"
    # Per-page files written
    written = list(per_page_dir.glob("*.md"))
    assert len(written) == 1
