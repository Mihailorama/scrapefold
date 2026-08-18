"""Tests for the TwinglyEngine (blog search, verified contract). Offline."""

from __future__ import annotations

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.twingly import TwinglyEngine, _build_query, _parse_response
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TWINGLYDATA = """<?xml version="1.0" encoding="utf-8"?>
<twinglydata numberOfMatchesReturned="2" secondsElapsed="0.148"
             numberOfMatchesTotal="3122" incompleteResult="false">
  <post>
    <id>16405819479794412880</id>
    <author>klara</author>
    <url>http://oppna-dorrar.example.com/2017/05/ai-agents.html</url>
    <title>AI agents in the wild</title>
    <text>Agents are eating the blogosphere.</text>
    <languageCode>en</languageCode>
    <locationCode>se</locationCode>
    <coordinates/>
    <links/>
    <tags>
      <tag>ai</tag>
      <tag>agents</tag>
    </tags>
    <images/>
    <indexedAt>2017-05-04T06:51:23Z</indexedAt>
    <publishedAt>2017-05-04T06:50:59Z</publishedAt>
    <reindexedAt>0001-01-01T00:00:00Z</reindexedAt>
    <inlinksCount>0</inlinksCount>
    <blogId>5312283800049632348</blogId>
    <blogName>oppna dorrar</blogName>
    <blogUrl>http://oppna-dorrar.example.com</blogUrl>
    <blogRank>1</blogRank>
    <authority>0</authority>
  </post>
  <post>
    <id>1234</id>
    <url>http://other.example.com/post</url>
    <title>Second post</title>
    <text>b</text>
    <coordinates>
      <latitude>49.1</latitude>
      <longitude>10.75</longitude>
    </coordinates>
    <inlinksCount>3</inlinksCount>
    <blogRank>2</blogRank>
    <authority>5</authority>
  </post>
</twinglydata>
"""

_ERROR = """<?xml version="1.0" encoding="utf-8"?>
<error code="40101"><message>Unauthorized</message></error>
"""


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,expected",
    [
        ("ai agents lang:en", "ai agents lang:en"),
        ("https://blog.example.com/some/post", "blog.url:https://blog.example.com"),
        ("blog.example.com", "blog.url:https://blog.example.com"),
    ],
)
def test_target_routing(target, expected) -> None:
    assert _build_query(target, ScrapeOptions()) == expected


def test_query_override_via_extra() -> None:
    q = _build_query(
        "https://blog.example.com/x",
        ScrapeOptions(extra={"twingly_q": "datatera", "twingly_page_size": 30}),
    )
    assert q == "datatera page-size:30"


def test_language_appended_once() -> None:
    assert _build_query("ai agents", ScrapeOptions(language="en")) == "ai agents lang:en"
    assert _build_query("ai lang:sv", ScrapeOptions(language="en")) == "ai lang:sv"


def test_extras_become_operators_sorted() -> None:
    q = _build_query(
        "phygital",
        ScrapeOptions(extra={"twingly_tspan": "24h", "twingly_sort": "published"}),
    )
    assert q == "phygital sort:published tspan:24h"


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


def test_parse_response_success() -> None:
    data = _parse_response(_TWINGLYDATA)
    assert data["numberOfMatchesReturned"] == 2
    assert data["numberOfMatchesTotal"] == 3122
    assert data["incompleteResult"] is False
    assert len(data["posts"]) == 2
    first, second = data["posts"]
    assert first["title"] == "AI agents in the wild"
    assert first["tags"] == ["ai", "agents"]
    assert first["blogRank"] == 1
    assert second["coordinates"] == {"latitude": 49.1, "longitude": 10.75}
    assert second["inlinksCount"] == 3


def test_parse_response_error_root() -> None:
    with pytest.raises(ValueError, match=r"40101.*Unauthorized"):
        _parse_response(_ERROR)


def test_parse_response_non_xml() -> None:
    with pytest.raises(ValueError, match="non-XML"):
        _parse_response("<html>rate limited</html")


# ---------------------------------------------------------------------------
# Fetch path
# ---------------------------------------------------------------------------


async def test_search_result_shape(httpx_mock) -> None:
    httpx_mock.add_response(content=_TWINGLYDATA.encode(), headers={"Content-Type": "text/xml"})

    engine = TwinglyEngine(api_key="twingly-test")
    result = await engine.scrape("ai agents lang:en")

    assert isinstance(result, ScrapeResult)
    assert result.engine == "twingly"
    assert result.json is not None and len(result.json["posts"]) == 2
    assert "AI agents in the wild" in result.markdown
    assert "oppna dorrar" in result.markdown
    assert "Agents are eating the blogosphere." in result.text
    assert result.meta["number_of_matches_total"] == 3122
    assert result.meta["twingly_query"] == "ai agents lang:en"


async def test_api_key_and_query_sent_as_params(httpx_mock) -> None:
    httpx_mock.add_response(content=_TWINGLYDATA.encode())
    engine = TwinglyEngine(api_key="secret-key")
    await engine.scrape("https://blog.example.com/post")
    request = httpx_mock.get_requests()[0]
    assert request.url.params["apiKey"] == "secret-key"
    assert request.url.params["q"] == "blog.url:https://blog.example.com"


async def test_timeout_respected(httpx_mock) -> None:
    httpx_mock.add_response(content=_TWINGLYDATA.encode())
    engine = TwinglyEngine(api_key="k")
    await engine.scrape("q", ScrapeOptions(timeout_s=7))
    timeouts = httpx_mock.get_requests()[0].extensions["timeout"]
    assert timeouts["connect"] == 7.0


# ---------------------------------------------------------------------------
# Errors / auth
# ---------------------------------------------------------------------------


async def test_inband_error_raises_engine_error(httpx_mock) -> None:
    httpx_mock.add_response(content=_ERROR.encode())
    engine = TwinglyEngine(api_key="bad")
    with pytest.raises(EngineError) as exc:
        await engine.scrape("ai agents")
    assert exc.value.engine == "twingly"
    assert "40101" in exc.value.message


async def test_http_error_wrapped(httpx_mock) -> None:
    httpx_mock.add_response(status_code=500)
    engine = TwinglyEngine(api_key="x")
    with pytest.raises(EngineError):
        await engine.scrape("ai agents")


def test_is_available() -> None:
    assert TwinglyEngine(api_key="t").is_available() is True


def test_is_available_false_without_key(monkeypatch) -> None:
    monkeypatch.delenv("TWINGLY_SEARCH_KEY", raising=False)
    assert TwinglyEngine(api_key=None).is_available() is False


def test_registry_resolves_twingly() -> None:
    from scrapefold.engines import get_engine

    assert get_engine("twingly") is TwinglyEngine
