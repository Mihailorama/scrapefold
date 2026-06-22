"""Tests for SocialCrawlEngine.

All tests use ``httpx_mock`` from pytest-httpx -- no real network calls.

Contract pinned here:
  - Method: GET
  - Base URL: https://www.socialcrawl.dev/v1
  - Auth: x-api-key: <api_key> header
  - URL -> endpoint routing plus endpoint gateway mode through extras
  - Response: JSON envelope -> ScrapeResult.json (+ text/markdown post-converted)
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

_BASE = "https://www.socialcrawl.dev/v1"
_API_KEY = "test-socialcrawl-key-123"

_PAYLOAD = {
    "success": True,
    "platform": "instagram",
    "endpoint": "/v1/instagram/profile",
    "data": {"username": "natgeo", "followers": 282000000},
    "credits_used": 1,
    "credits_remaining": 99,
    "request_id": "req-test-123",
    "cached": False,
}


def _engine(api_key: str = _API_KEY):
    from scrapefold.engines.socialcrawl import SocialCrawlEngine

    return SocialCrawlEngine(api_key=api_key)


def _mock(httpx_mock: HTTPXMock, *, payload: dict | None = None, status_code: int = 200) -> None:
    httpx_mock.add_response(
        method="GET",
        status_code=status_code,
        json=payload if payload is not None else _PAYLOAD,
    )


async def test_basic_200_populates_json_text_markdown_and_meta(
    httpx_mock: HTTPXMock,
) -> None:
    _mock(httpx_mock)
    result = await _engine().scrape("https://www.instagram.com/natgeo/")

    assert result.json == _PAYLOAD
    assert result.html is None
    assert "natgeo" in result.text
    assert "natgeo" in result.markdown
    assert result.markdown.startswith("```json")
    assert result.cost_usd == 0.002
    assert result.engine == "socialcrawl"
    assert result.meta == {
        "status_code": 200,
        "socialcrawl_endpoint": "/instagram/profile",
        "socialcrawl_credits_used": 1,
        "socialcrawl_credits_remaining": 99,
        "socialcrawl_request_id": "req-test-123",
        "socialcrawl_cached": False,
    }


async def test_api_key_header_set(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    await _engine().scrape("https://www.instagram.com/natgeo/")

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-api-key") == _API_KEY


@pytest.mark.parametrize(
    "url,endpoint,param,value",
    [
        ("https://www.tiktok.com/@user/video/123", "/tiktok/post", "url", None),
        ("https://www.tiktok.com/@stoolpresidente", "/tiktok/profile", "handle", "stoolpresidente"),
        ("https://www.instagram.com/p/Cabc123/", "/instagram/post", "url", None),
        ("https://www.instagram.com/reel/Cxyz/", "/instagram/post", "url", None),
        ("https://www.instagram.com/natgeo/", "/instagram/profile", "handle", "natgeo"),
        ("https://www.youtube.com/watch?v=abc", "/youtube/video", "url", None),
        ("https://youtu.be/abc", "/youtube/video", "url", None),
        ("https://www.youtube.com/shorts/abc", "/youtube/video", "url", None),
        (
            "https://www.youtube.com/@ThePatMcAfeeShow",
            "/youtube/channel",
            "handle",
            "ThePatMcAfeeShow",
        ),
        ("https://www.facebook.com/natgeo", "/facebook/profile", "url", None),
        ("https://www.facebook.com/natgeo/posts/123", "/facebook/post", "url", None),
        ("https://x.com/Austen/status/1", "/twitter/tweet", "url", None),
        ("https://twitter.com/Austen", "/twitter/profile", "handle", "Austen"),
        ("https://www.linkedin.com/in/jane-doe/", "/linkedin/profile", "url", None),
        ("https://www.linkedin.com/company/openai/", "/linkedin/company", "url", None),
        ("https://www.linkedin.com/posts/foo_activity-1", "/linkedin/post", "url", None),
        (
            "https://www.reddit.com/r/python/comments/abc/title/",
            "/reddit/post/comments",
            "url",
            None,
        ),
    ],
)
def test_route_for_maps_urls(url: str, endpoint: str, param: str, value: str | None) -> None:
    from scrapefold.engines.socialcrawl import _route_for

    got_endpoint, params = _route_for(url)
    assert got_endpoint == endpoint
    assert param in params
    if value is not None:
        assert params[param] == value
    else:
        assert params["url"] == url


async def test_routing_hits_correct_endpoint(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    await _engine().scrape("https://www.instagram.com/natgeo/")

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{_BASE}/instagram/profile")
    assert request.url.params.get("handle") == "natgeo"


def test_route_for_unknown_platform_raises() -> None:
    from scrapefold.engines.socialcrawl import _route_for

    with pytest.raises(ValueError, match="no endpoint"):
        _route_for("https://example.com/blog/post")


async def test_unknown_url_raises_engine_error() -> None:
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/blog/post")
    assert exc_info.value.engine == "socialcrawl"


async def test_extra_endpoint_override(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"socialcrawl_endpoint": "/instagram/profile"})
    await _engine().scrape("https://example.com/anything", opts)

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{_BASE}/instagram/profile")
    assert request.url.params.get("url") == "https://example.com/anything"


async def test_extra_endpoint_override_accepts_v1_prefix(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"socialcrawl_endpoint": "/v1/youtube/video"})
    await _engine().scrape("https://youtu.be/abc", opts)

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{_BASE}/youtube/video")


def test_adapt_endpoint_override_with_target_param_skips_url() -> None:
    from scrapefold.engines.socialcrawl import _adapt

    opts = ScrapeOptions(
        extra={
            "socialcrawl_endpoint": "/tiktok/profile",
            "socialcrawl_handle": "someone",
        }
    )
    endpoint, params = _adapt(opts, "https://whatever")
    assert endpoint == "/tiktok/profile"
    assert params == {"handle": "someone"}
    assert "url" not in params


async def test_extra_params_forwarded(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"socialcrawl_trim": "true"})
    await _engine().scrape("https://x.com/Austen/status/1", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("trim") == "true"


def test_adapt_ignores_non_socialcrawl_extra_keys() -> None:
    from scrapefold.engines.socialcrawl import _adapt

    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    _endpoint, params = _adapt(opts, "https://twitter.com/Austen")
    assert "foo" not in params
    assert "unrelated" not in params


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from scrapefold.engines.socialcrawl import SocialCrawlEngine

    monkeypatch.delenv("SOCIALCRAWL_API_KEY", raising=False)
    assert SocialCrawlEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from scrapefold.engines.socialcrawl import SocialCrawlEngine

    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "env-key")
    assert SocialCrawlEngine().api_key == "env-key"


@pytest.mark.parametrize("status_code", [401, 402, 404, 429, 500, 502, 503])
async def test_http_error_raises_engine_error(
    httpx_mock: HTTPXMock,
    status_code: int,
) -> None:
    _mock(httpx_mock, payload={"success": False}, status_code=status_code)
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://twitter.com/Austen")
    assert exc_info.value.engine == "socialcrawl"


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://twitter.com/Austen")
    assert exc_info.value.engine == "socialcrawl"


async def test_unsupported_options_dropped(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(render_js=False, stealth=True, country="us", wait_ms=9000)
    result = await _engine().scrape("https://twitter.com/Austen", opts)
    assert result.engine == "socialcrawl"


def test_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names
    from scrapefold.engines.socialcrawl import SocialCrawlEngine

    assert "socialcrawl" in list_engine_names()
    assert get_engine("socialcrawl") is SocialCrawlEngine
