"""Tests for DuckDuckGoSearchEngine. Canned HTML fed via pytest-httpx; no live calls."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.search.engines.base import SearchEngineError
from scrapefold.search.options import SearchOptions

_CANNED_HTML = """
<html><body>
  <div class="result">
    <a class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal-one.com%2Fpage&amp;rut=abc">First Result</a>
    <a class="result__snippet"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal-one.com%2Fpage">This is the first snippet.</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://direct-two.com/">Second Result</a>
    <a class="result__snippet">Second snippet here.</a>
  </div>
</body></html>
"""


def _engine():
    from scrapefold.search.engines.duckduckgo import DuckDuckGoSearchEngine

    return DuckDuckGoSearchEngine()


async def test_success_parses_hits_and_unwraps_redirects(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="GET", text=_CANNED_HTML)

    hits = await engine.search("privacy search")

    assert [h.url for h in hits] == ["https://real-one.com/page", "https://direct-two.com/"]
    assert hits[0].title == "First Result"
    assert hits[0].snippet == "This is the first snippet."
    assert hits[1].title == "Second Result"
    assert hits[1].snippet == "Second snippet here."
    assert [h.rank for h in hits] == [0, 1]
    assert all(h.engine == "duckduckgo" for h in hits)


async def test_request_carries_query_and_user_agent(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="GET", text=_CANNED_HTML)

    await engine.search("some query")

    request = httpx_mock.get_requests()[0]
    assert request.url.params["q"] == "some query"
    assert "Mozilla/5.0" in request.headers["User-Agent"]


async def test_count_truncates_results(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="GET", text=_CANNED_HTML)

    hits = await engine.search("q", SearchOptions(count=1))

    assert len(hits) == 1
    assert hits[0].url == "https://real-one.com/page"


async def test_unparseable_html_returns_empty_list(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="GET", text="<html><body>no results here</body></html>")

    assert await engine.search("q") == []


async def test_http_error_raises_search_engine_error(httpx_mock: HTTPXMock) -> None:
    engine = _engine()
    httpx_mock.add_response(method="GET", status_code=503, text="down")

    with pytest.raises(SearchEngineError) as exc_info:
        await engine.search("q")

    assert exc_info.value.engine == "duckduckgo"


def test_is_available_true_without_key() -> None:
    assert _engine().is_available() is True


def test_engine_registered_with_alias() -> None:
    from scrapefold.search.engines import get_search_engine, list_search_engine_names

    assert "duckduckgo" in list_search_engine_names()
    assert get_search_engine("duckduckgo").__name__ == "DuckDuckGoSearchEngine"
    # "ddg" alias resolves to the same engine.
    assert get_search_engine("ddg").__name__ == "DuckDuckGoSearchEngine"
