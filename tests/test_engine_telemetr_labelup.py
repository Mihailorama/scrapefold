"""Tests for the best-effort TelemetrEngine and gateway LabelUpEngine. Offline.

These pin the *adapter mechanics* (routing/auth/unwrap/normalization), not the
real vendor contract — which is unverified and overridable by design.
"""

from __future__ import annotations

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult
from scrapefold.social import Post, Profile

# ---------------------------------------------------------------------------
# Telemetr
# ---------------------------------------------------------------------------


def test_telemetr_routing() -> None:
    from scrapefold.engines.telemetr import _route_for

    assert _route_for("https://t.me/durov") == ("durov", "profile", None)
    assert _route_for("https://t.me/s/durov") == ("durov", "posts", None)
    assert _route_for("https://t.me/durov/151") == ("durov", "post", "151")


def test_telemetr_build_request() -> None:
    from scrapefold.engines.telemetr import _build_request

    ep, params, kind = _build_request("profile", "42", None, {})
    assert ep == "/channel/info"
    assert params == {"internal_id": "42"}
    assert kind == "profile"

    ep, params, kind = _build_request("posts", "42", None, {})
    assert ep == "/messages/channel"
    assert kind == "post"

    ep, params, kind = _build_request("post", "42", "151", {})
    assert ep == "/messages/by_id"
    assert params == {"internal_id": "42", "message_id": "151"}
    assert kind == "post"


async def test_telemetr_channel_profile(httpx_mock) -> None:
    from scrapefold.engines.telemetr import TelemetrEngine

    # Step 1: search resolves the slug to an internal_id.
    httpx_mock.add_response(json={"items": [{"internal_id": 777, "username": "durov"}]})
    # Step 2: channel info by internal_id.
    httpx_mock.add_response(
        json={"data": {"username": "durov", "title": "Durov", "subscribers_count": 1000}},
    )
    engine = TelemetrEngine(api_key="telemetr-test")
    result = await engine.scrape("https://t.me/durov")

    assert isinstance(result, ScrapeResult)
    assert result.engine == "telemetr"
    assert isinstance(result.social, Profile)
    assert result.social.platform == "telegram"
    assert result.social.followers == 1000

    search, info = httpx_mock.get_requests()
    assert search.url.path.endswith("/channels/search")
    assert search.url.params["term"] == "durov"
    assert info.url.path.endswith("/channel/info")
    assert info.url.params["internal_id"] == "777"


async def test_telemetr_internal_id_skips_search(httpx_mock) -> None:
    # When the caller supplies internal_id, no search call is made.
    from scrapefold.engines.telemetr import TelemetrEngine

    httpx_mock.add_response(json={"data": {"username": "durov", "subscribers_count": 5}})
    engine = TelemetrEngine(api_key="x")
    opts = ScrapeOptions(extra={"telemetr_internal_id": "999"})
    result = await engine.scrape("https://t.me/durov", opts)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1  # no search round-trip
    assert requests[0].url.params["internal_id"] == "999"
    assert isinstance(result.social, Profile)


async def test_telemetr_forced_endpoint_single_call(httpx_mock) -> None:
    from scrapefold.engines.telemetr import TelemetrEngine

    httpx_mock.add_response(json={"items": [{"username": "x"}]})
    engine = TelemetrEngine(api_key="x")
    opts = ScrapeOptions(extra={"telemetr_endpoint": "/catalog/search", "telemetr_term": "ai"})
    result = await engine.scrape("https://t.me/durov", opts)

    requests = httpx_mock.get_requests()
    assert len(requests) == 1  # forced path -> no resolution
    assert requests[0].url.path.endswith("/catalog/search")
    assert requests[0].url.params["term"] == "ai"
    assert result.engine == "telemetr"


async def test_telemetr_xapikey_auth(httpx_mock) -> None:
    from scrapefold.engines.telemetr import TelemetrEngine

    httpx_mock.add_response(json={"items": [{"internal_id": 1}]})
    httpx_mock.add_response(json={"data": {"username": "d"}})
    await TelemetrEngine(api_key="tok").scrape("https://t.me/durov")
    request = httpx_mock.get_requests()[0]
    assert request.headers["x-api-key"] == "tok"
    assert "Authorization" not in request.headers


async def test_telemetr_http_error_wrapped(httpx_mock) -> None:
    from scrapefold.engines.telemetr import TelemetrEngine

    httpx_mock.add_response(status_code=403)
    with pytest.raises(EngineError):
        await TelemetrEngine(api_key="x").scrape("https://t.me/durov")


# ---------------------------------------------------------------------------
# LabelUp (verified /accounts/statistics routing)
# ---------------------------------------------------------------------------


def test_labelup_default_route_uses_url_param() -> None:
    from scrapefold.engines.labelup import _adapt

    ep, params, kind, platform = _adapt(ScrapeOptions(), "https://t.me/durov")
    assert ep == "/accounts/statistics"
    assert params == {"url": "https://t.me/durov"}
    assert kind == "profile"
    assert platform == "telegram"


def test_labelup_nickname_route_derives_network_id() -> None:
    from scrapefold.engines.labelup import _adapt

    ep, params, _kind, platform = _adapt(
        ScrapeOptions(extra={"labelup_nickname": "nasa"}),
        "https://www.instagram.com/nasa/",
    )
    assert ep == "/accounts/statistics"
    # Pinned by nickname -> no url param, network_id derived from platform.
    assert "url" not in params
    assert params["nickname"] == "nasa"
    assert params["network_id"] == "1"  # instagram
    assert platform == "instagram"


def test_labelup_forced_endpoint_is_raw_gateway() -> None:
    from scrapefold.engines.labelup import _adapt

    ep, params, kind, _platform = _adapt(
        ScrapeOptions(extra={"labelup_endpoint": "/custom/x", "labelup_limit": 5}),
        "https://t.me/durov",
    )
    assert ep == "/custom/x"
    assert params == {"limit": "5"}
    assert kind is None  # no kind hint in raw gateway mode


async def test_labelup_default_route_profile(httpx_mock) -> None:
    from scrapefold.engines.labelup import LabelUpEngine

    httpx_mock.add_response(json={"nickname": "durov", "followers_count": 1000})
    engine = LabelUpEngine(api_key="labelup-test")
    result = await engine.scrape("https://t.me/durov")

    assert result.engine == "labelup"
    assert isinstance(result.social, Profile)
    assert result.social.platform == "telegram"
    assert result.social.followers == 1000

    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/api/v2/accounts/statistics"
    assert request.url.params["url"] == "https://t.me/durov"


async def test_labelup_gateway_post(httpx_mock) -> None:
    from scrapefold.engines.labelup import LabelUpEngine

    httpx_mock.add_response(
        json={"data": [{"id": 1, "text": "hi", "views": 10}]},
    )
    engine = LabelUpEngine(api_key="labelup-test")
    opts = ScrapeOptions(
        extra={"labelup_endpoint": "/channels/durov/posts", "labelup_kind": "post"}
    )
    result = await engine.scrape("https://t.me/durov", opts)

    assert result.engine == "labelup"
    assert isinstance(result.social, list)
    assert isinstance(result.social[0], Post)
    assert result.social[0].text == "hi"


async def test_labelup_platform_inferred_from_url(httpx_mock) -> None:
    # LabelUp spans many platforms — the platform must come from the URL host,
    # not be hard-coded to telegram.
    from scrapefold.engines.labelup import LabelUpEngine

    httpx_mock.add_response(json={"username": "nasa", "followersCount": 90})
    engine = LabelUpEngine(api_key="x")
    result = await engine.scrape("https://www.instagram.com/nasa/")

    assert isinstance(result.social, Profile)
    assert result.social.platform == "instagram"
    assert result.meta["labelup_platform"] == "instagram"


async def test_labelup_platform_override(httpx_mock) -> None:
    from scrapefold.engines.labelup import LabelUpEngine

    httpx_mock.add_response(json={"username": "x", "followersCount": 1})
    engine = LabelUpEngine(api_key="x")
    opts = ScrapeOptions(extra={"labelup_platform": "youtube"})
    result = await engine.scrape("https://example.com/x", opts)
    assert result.social.platform == "youtube"


async def test_labelup_bearer_auth_and_xhr_header(httpx_mock) -> None:
    from scrapefold.engines.labelup import LabelUpEngine

    httpx_mock.add_response(json={"username": "d"})
    await LabelUpEngine(api_key="tok").scrape("https://t.me/durov")
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer tok"
    assert request.headers["X-Requested-With"] == "XMLHttpRequest"


def test_registry_resolves_telemetr_and_labelup() -> None:
    from scrapefold.engines import get_engine
    from scrapefold.engines.labelup import LabelUpEngine
    from scrapefold.engines.telemetr import TelemetrEngine

    assert get_engine("telemetr") is TelemetrEngine
    assert get_engine("labelup") is LabelUpEngine
