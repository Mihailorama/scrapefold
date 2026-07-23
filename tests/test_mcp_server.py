"""Tests for the scrapefold MCP server (scrapefold-mcp)."""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest

from scrapefold import mcp_server
from scrapefold.result import ScrapeResult

requires_mcp = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="mcp extra not installed (pip install 'scrapefold[mcp]')",
)


# ---------------------------------------------------------------------------
# 1. main() exits 2 with an install hint when the mcp extra is missing
# ---------------------------------------------------------------------------


def test_main_exits_with_install_hint_when_mcp_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise() -> Any:
        raise ImportError("No module named 'mcp'")

    monkeypatch.setattr(mcp_server, "build_server", _raise)

    with pytest.raises(SystemExit) as excinfo:
        mcp_server.main()

    assert excinfo.value.code == 2
    assert 'pip install "scrapefold[mcp]"' in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 2. _result_payload drops bulky slots
# ---------------------------------------------------------------------------


def test_result_payload_drops_screenshot_and_oversized_html() -> None:
    result = ScrapeResult(
        url="https://example.com/",
        text="hello",
        markdown="# hello",
        html="x" * (mcp_server._MAX_HTML_CHARS + 1),
        engine="stub",
        elapsed_ms=1,
    )
    payload = mcp_server._result_payload(result)
    assert "screenshot_b64" not in payload
    assert payload["html"] is None
    assert payload["markdown"] == "# hello"


def test_result_payload_keeps_small_html() -> None:
    result = ScrapeResult(
        url="https://example.com/",
        text="hello",
        markdown="# hello",
        html="<p>hello</p>",
        engine="stub",
        elapsed_ms=1,
    )
    payload = mcp_server._result_payload(result)
    assert payload["html"] == "<p>hello</p>"


# ---------------------------------------------------------------------------
# 3. build_server registers exactly the four documented tools
# ---------------------------------------------------------------------------


@requires_mcp
async def test_build_server_registers_four_tools() -> None:
    server = mcp_server.build_server()
    tools = await server.list_tools()
    assert {t.name for t in tools} == {
        "scrape_url",
        "crawl_site",
        "list_engines",
        "classify_url",
    }


# ---------------------------------------------------------------------------
# 4. scrape_url tool → ScrapeResult payload, engines forwarded
# ---------------------------------------------------------------------------


@requires_mcp
async def test_scrape_url_tool_returns_markdown_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_scrape(url: str, opts: Any = None) -> ScrapeResult:
        captured["url"] = url
        captured["engines"] = opts.engines
        captured["stealth"] = opts.stealth
        return ScrapeResult(
            url=url,
            text="hello",
            markdown="# hello",
            html=None,
            engine="stub",
            elapsed_ms=3,
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    server = mcp_server.build_server()
    _content, structured = await server.call_tool(
        "scrape_url",
        {"url": "https://example.com/", "engines": "jina,firecrawl", "stealth": True},
    )

    assert captured["url"] == "https://example.com/"
    assert captured["engines"] == ("jina", "firecrawl")
    assert captured["stealth"] is True
    assert structured["markdown"] == "# hello"
    assert structured["engine"] == "stub"


# ---------------------------------------------------------------------------
# 5. crawl_site tool → stitched markdown + page list
# ---------------------------------------------------------------------------


@requires_mcp
async def test_crawl_site_tool_returns_stitched_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from scrapefold.crawler.result import CrawlResult

    stitched = tmp_path / "site.md"
    stitched.write_text("# stitched")

    pages = (
        ScrapeResult(
            url="https://example.com/a",
            text="A",
            markdown="# A",
            html=None,
            engine="stub",
            elapsed_ms=1,
        ),
    )

    async def _fake_crawl(url: str, opts: Any = None, **_kw: Any) -> CrawlResult:
        assert opts.max_pages == 5
        return CrawlResult(pages=pages, stitched_path=stitched, failures=("u:E:x",))

    monkeypatch.setattr("scrapefold.crawl_site", _fake_crawl)

    server = mcp_server.build_server()
    _content, structured = await server.call_tool(
        "crawl_site", {"url": "https://example.com/", "max_pages": 5}
    )

    assert structured["markdown"] == "# stitched"
    assert structured["pages"] == [{"url": "https://example.com/a", "engine": "stub"}]
    assert structured["failures"] == ["u:E:x"]


# ---------------------------------------------------------------------------
# 6. list_engines / classify_url tools
# ---------------------------------------------------------------------------


@requires_mcp
async def test_list_engines_tool() -> None:
    server = mcp_server.build_server()
    _content, structured = await server.call_tool("list_engines", {})
    names = structured["result"] if isinstance(structured, dict) else structured
    assert "requests" in names


@requires_mcp
async def test_classify_url_tool() -> None:
    server = mcp_server.build_server()
    _content, structured = await server.call_tool(
        "classify_url", {"url": "https://www.linkedin.com/in/someone"}
    )
    assert structured["site_class"] == "linkedin_profile"


# ---------------------------------------------------------------------------
# 7. scrape_url focus= trims the payload
# ---------------------------------------------------------------------------


@requires_mcp
async def test_scrape_url_focus_filters_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    long_md = "## Pricing\n\nThe plan costs 42 dollars.\n\n## Other\n\nUnrelated text here."

    async def _fake_scrape(url: str, opts: Any = None) -> ScrapeResult:
        return ScrapeResult(
            url=url,
            text="full text",
            markdown=long_md,
            html="<p>full html</p>",
            engine="stub",
            elapsed_ms=1,
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)

    server = mcp_server.build_server()
    _content, structured = await server.call_tool(
        "scrape_url",
        {"url": "https://example.com/", "focus": "price dollars cost"},
    )

    assert "42 dollars" in structured["markdown"]
    assert "Unrelated text" not in structured["markdown"]
    assert structured["text"] == ""
    assert structured["html"] is None
    assert structured["focus"] == "price dollars cost"
