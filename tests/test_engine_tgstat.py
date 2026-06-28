"""Tests for the TGStatEngine (Telegram analytics, verified contract). Offline."""

from __future__ import annotations

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.tgstat import TGStatEngine, _adapt
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult
from scrapefold.social import Post, Profile

_CHANNEL = {
    "status": "ok",
    "response": {
        "id": 53248,
        "link": "t.me/durov",
        "username": "@durov",
        "title": "Du Rove's Channel",
        "about": "Thoughts from the founder.",
        "participants_count": 1_200_000,
    },
}

_POSTS = {
    "status": "ok",
    "response": {
        "count": 2,
        "total_count": 9000,
        "items": [
            {"id": 151, "date": 1700000000, "views": 500000, "link": "t.me/durov/151", "text": "a"},
            {"id": 152, "date": 1700001000, "views": 600000, "link": "t.me/durov/152", "text": "b"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,endpoint,kind,param_key,param_val",
    [
        ("https://t.me/durov", "/channels/get", "profile", "channelId", "@durov"),
        ("https://t.me/s/durov", "/channels/posts", "post", "channelId", "@durov"),
        ("https://t.me/durov/151", "/posts/get", "post", "postId", "151"),
    ],
)
def test_routing(url, endpoint, kind, param_key, param_val) -> None:
    ep, params, k = _adapt(ScrapeOptions(), url)
    assert ep == endpoint
    assert k == kind
    assert params[param_key] == param_val


def test_forced_endpoint_via_extra() -> None:
    ep, params, _kind = _adapt(
        ScrapeOptions(extra={"tgstat_endpoint": "/channels/stat", "tgstat_extended": "1"}),
        "https://t.me/durov",
    )
    assert ep == "/channels/stat"
    assert params["extended"] == "1"


def test_non_telegram_url_raises() -> None:
    with pytest.raises(ValueError, match="non-Telegram"):
        _adapt(ScrapeOptions(), "https://example.com/foo")


# ---------------------------------------------------------------------------
# Channel -> Profile
# ---------------------------------------------------------------------------


async def test_channel_normalized_to_profile(httpx_mock) -> None:
    httpx_mock.add_response(json=_CHANNEL)

    engine = TGStatEngine(api_key="tgstat-test")
    result = await engine.scrape("https://t.me/durov")

    assert isinstance(result, ScrapeResult)
    assert result.engine == "tgstat"
    assert result.json == _CHANNEL
    assert isinstance(result.social, Profile)
    assert result.social.platform == "telegram"
    assert result.social.handle == "@durov"
    assert result.social.name == "Du Rove's Channel"
    assert result.social.followers == 1_200_000
    assert result.social.bio == "Thoughts from the founder."


# ---------------------------------------------------------------------------
# Feed -> list[Post]
# ---------------------------------------------------------------------------


async def test_feed_normalized_to_posts(httpx_mock) -> None:
    httpx_mock.add_response(json=_POSTS)

    engine = TGStatEngine(api_key="tgstat-test")
    result = await engine.scrape("https://t.me/s/durov")

    assert isinstance(result.social, list)
    assert len(result.social) == 2
    assert all(isinstance(p, Post) for p in result.social)
    assert result.social[0].id == "151"
    assert result.social[0].view_count == 500000
    assert result.social[0].url == "t.me/durov/151"
    assert result.social[0].created_at == 1700000000


# ---------------------------------------------------------------------------
# Errors / auth
# ---------------------------------------------------------------------------


async def test_inband_error_status_raises(httpx_mock) -> None:
    httpx_mock.add_response(json={"status": "error", "error": "token invalid"})
    engine = TGStatEngine(api_key="bad")
    with pytest.raises(EngineError) as exc:
        await engine.scrape("https://t.me/durov")
    assert exc.value.engine == "tgstat"


async def test_http_error_wrapped(httpx_mock) -> None:
    httpx_mock.add_response(status_code=500)
    engine = TGStatEngine(api_key="x")
    with pytest.raises(EngineError):
        await engine.scrape("https://t.me/durov")


async def test_token_sent_as_query_param(httpx_mock) -> None:
    httpx_mock.add_response(json=_CHANNEL)
    engine = TGStatEngine(api_key="secret-token")
    await engine.scrape("https://t.me/durov")
    request = httpx_mock.get_requests()[0]
    assert request.url.params["token"] == "secret-token"


def test_is_available() -> None:
    assert TGStatEngine(api_key="t").is_available() is True


def test_is_available_false_without_token(monkeypatch) -> None:
    monkeypatch.delenv("TGSTAT_API_TOKEN", raising=False)
    assert TGStatEngine(api_key=None).is_available() is False


def test_registry_resolves_tgstat() -> None:
    from scrapefold.engines import get_engine

    assert get_engine("tgstat") is TGStatEngine
