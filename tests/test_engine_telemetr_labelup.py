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
    from scrapefold.engines.telemetr import _adapt

    ep, _params, kind = _adapt(ScrapeOptions(), "https://t.me/durov")
    assert ep == "/channels/@durov"
    assert kind == "profile"

    ep, _params, kind = _adapt(ScrapeOptions(), "https://t.me/s/durov")
    assert ep == "/channels/@durov/posts"
    assert kind == "post"

    ep, _params, kind = _adapt(ScrapeOptions(), "https://t.me/durov/151")
    assert ep == "/posts/151"
    assert kind == "post"


def test_telemetr_forced_endpoint() -> None:
    from scrapefold.engines.telemetr import _adapt

    ep, _params, kind = _adapt(
        ScrapeOptions(extra={"telemetr_endpoint": "/custom/x"}), "https://t.me/durov"
    )
    assert ep == "/custom/x"
    assert kind is None


async def test_telemetr_channel_profile(httpx_mock) -> None:
    from scrapefold.engines.telemetr import TelemetrEngine

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


async def test_telemetr_bearer_auth(httpx_mock) -> None:
    from scrapefold.engines.telemetr import TelemetrEngine

    httpx_mock.add_response(json={"data": {"username": "d"}})
    await TelemetrEngine(api_key="tok").scrape("https://t.me/durov")
    request = httpx_mock.get_requests()[0]
    assert request.headers["Authorization"] == "Bearer tok"


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
