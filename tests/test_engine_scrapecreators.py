"""Tests for ScrapeCreatorsEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.

Contract pinned here:
  - Method: GET
  - Base URL: https://api.scrapecreators.com
  - Auth: x-api-key: <api_key> header
  - URL → endpoint routing (see _route_for)
  - Response: JSON object → ScrapeResult.json (+ text/markdown post-converted)
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.scrapecreators import ScrapeCreatorsEngine, _adapt, _route_for
from scrapefold.options import ScrapeOptions

_BASE = "https://api.scrapecreators.com"
_API_KEY = "test-sc-key-123"

_PAYLOAD = {"user": {"uniqueId": "stoolpresidente", "nickname": "Dave"}, "stats": {"heart": 42}}


def _engine(api_key: str = _API_KEY) -> ScrapeCreatorsEngine:
    return ScrapeCreatorsEngine(api_key=api_key)


def _mock(httpx_mock: HTTPXMock, *, payload: dict | None = None, status_code: int = 200) -> None:
    httpx_mock.add_response(
        method="GET",
        status_code=status_code,
        json=payload if payload is not None else _PAYLOAD,
    )


# ---------------------------------------------------------------------------
# 1. Basic 200 JSON → json slot + text/markdown populated; cost set
# ---------------------------------------------------------------------------


async def test_basic_200_populates_json_text_markdown(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    result = await _engine().scrape("https://www.tiktok.com/@stoolpresidente")

    assert result.json == _PAYLOAD
    assert result.html is None
    assert "stoolpresidente" in result.text
    assert "stoolpresidente" in result.markdown
    assert result.markdown.startswith("```json")
    assert result.cost_usd == 0.002
    assert result.engine == "scrapecreators"


# ---------------------------------------------------------------------------
# 2. x-api-key header set on the outgoing request
# ---------------------------------------------------------------------------


async def test_api_key_header_set(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    await _engine().scrape("https://www.tiktok.com/@stoolpresidente")

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-api-key") == _API_KEY


# ---------------------------------------------------------------------------
# 3. URL → endpoint routing (the adapter matrix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,endpoint,param,value",
    [
        ("https://www.tiktok.com/@user/video/123", "/v1/tiktok/video", "url", None),
        (
            "https://www.tiktok.com/@stoolpresidente",
            "/v1/tiktok/profile",
            "handle",
            "stoolpresidente",
        ),
        ("https://www.instagram.com/p/Cabc123/", "/v1/instagram/post", "url", None),
        ("https://www.instagram.com/reel/Cxyz/", "/v1/instagram/post", "url", None),
        ("https://www.instagram.com/natgeo/", "/v1/instagram/profile", "handle", "natgeo"),
        ("https://www.youtube.com/watch?v=abc", "/v1/youtube/video", "url", None),
        ("https://youtu.be/abc", "/v1/youtube/video", "url", None),
        ("https://www.youtube.com/shorts/abc", "/v1/youtube/video", "url", None),
        ("https://www.youtube.com/@ThePatMcAfeeShow", "/v1/youtube/channel", "url", None),
        ("https://x.com/Austen/status/1", "/v1/twitter/tweet", "url", None),
        ("https://twitter.com/Austen", "/v1/twitter/profile", "handle", "Austen"),
        (
            "https://www.reddit.com/r/python/comments/abc/title/",
            "/v1/reddit/post",
            "url",
            None,
        ),
    ],
)
def test_route_for_maps_urls(url: str, endpoint: str, param: str, value: str | None) -> None:
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
    assert str(request.url).startswith(f"{_BASE}/v1/instagram/profile")
    assert request.url.params.get("handle") == "natgeo"


# ---------------------------------------------------------------------------
# 4. Unroutable URL → EngineError (wrapped ValueError)
# ---------------------------------------------------------------------------


def test_route_for_unknown_platform_raises() -> None:
    with pytest.raises(ValueError, match="no endpoint"):
        _route_for("https://example.com/blog/post")


async def test_unknown_url_raises_engine_error() -> None:
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/blog/post")
    assert exc_info.value.engine == "scrapecreators"


# ---------------------------------------------------------------------------
# 5. extra["scrapecreators_endpoint"] forces the endpoint path
# ---------------------------------------------------------------------------


async def test_extra_endpoint_override(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"scrapecreators_endpoint": "/v1/youtube/channel"})
    await _engine().scrape("https://example.com/anything", opts)

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{_BASE}/v1/youtube/channel")
    # url auto-added because no target param supplied via extras
    assert request.url.params.get("url") == "https://example.com/anything"


def test_adapt_endpoint_override_with_target_param_skips_url() -> None:
    opts = ScrapeOptions(
        extra={
            "scrapecreators_endpoint": "/v1/tiktok/profile",
            "scrapecreators_handle": "someone",
        }
    )
    endpoint, params = _adapt(opts, "https://whatever")
    assert endpoint == "/v1/tiktok/profile"
    assert params == {"handle": "someone"}
    assert "url" not in params


# ---------------------------------------------------------------------------
# 6. extra["scrapecreators_*"] forwarded as query params (prefix stripped)
# ---------------------------------------------------------------------------


async def test_extra_params_forwarded(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"scrapecreators_trim": "true"})
    await _engine().scrape("https://x.com/Austen/status/1", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("trim") == "true"


def test_adapt_ignores_non_scrapecreators_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    _endpoint, params = _adapt(opts, "https://twitter.com/Austen")
    assert "foo" not in params
    assert "unrelated" not in params


# ---------------------------------------------------------------------------
# 7. meta carries status_code + endpoint
# ---------------------------------------------------------------------------


async def test_meta_has_status_and_endpoint(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    result = await _engine().scrape("https://twitter.com/Austen")

    assert result.meta["status_code"] == 200
    assert result.meta["scrapecreators_endpoint"] == "/v1/twitter/profile"


# ---------------------------------------------------------------------------
# 8. is_available() True with key, False without
# ---------------------------------------------------------------------------


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAPECREATORS_API_KEY", raising=False)
    assert ScrapeCreatorsEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "env-key")
    assert ScrapeCreatorsEngine().api_key == "env-key"


# ---------------------------------------------------------------------------
# 9. Errors → EngineError
# ---------------------------------------------------------------------------


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    """402 out-of-credits / 401 bad key must surface as EngineError."""
    _mock(httpx_mock, payload={}, status_code=402)
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://twitter.com/Austen")
    assert exc_info.value.engine == "scrapecreators"


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://twitter.com/Austen")
    assert exc_info.value.engine == "scrapecreators"


# ---------------------------------------------------------------------------
# 10. Unsupported options are dropped (don't crash)
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(render_js=False, stealth=True, country="us", wait_ms=9000)
    result = await _engine().scrape("https://twitter.com/Austen", opts)
    assert result.engine == "scrapecreators"


# ---------------------------------------------------------------------------
# 11. Registry wiring
# ---------------------------------------------------------------------------


def test_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "scrapecreators" in list_engine_names()
    assert get_engine("scrapecreators") is ScrapeCreatorsEngine
