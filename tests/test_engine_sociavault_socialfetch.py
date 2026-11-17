"""Offline tests for SociaVaultEngine and SocialFetchEngine (pytest-httpx)."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.socialfetch import SocialFetchEngine, _sf_route
from scrapefold.engines.sociavault import SociaVaultEngine, _sv_route
from scrapefold.social import Profile


@pytest.mark.parametrize(
    ("url", "endpoint", "params"),
    [
        (
            "https://www.tiktok.com/@a/video/1",
            "/v1/scrape/tiktok/video-info",
            {"url": "https://www.tiktok.com/@a/video/1"},
        ),
        (
            "https://www.instagram.com/p/Cx1/",
            "/v1/scrape/instagram/post-info",
            {"url": "https://www.instagram.com/p/Cx1/"},
        ),
        ("https://x.com/nasa", "/v1/scrape/twitter/profile", {"handle": "nasa"}),
        (
            "https://www.reddit.com/r/x/comments/a/t/",
            "/v1/scrape/reddit/post/comments",
            {"url": "https://www.reddit.com/r/x/comments/a/t/"},
        ),
    ],
)
def test_sociavault_routes(url: str, endpoint: str, params: dict[str, str]) -> None:
    assert _sv_route(url) == (endpoint, params)


@pytest.mark.parametrize(
    ("url", "endpoint", "params"),
    [
        (
            "https://www.tiktok.com/@a/video/1",
            "/v1/tiktok/videos",
            {"url": "https://www.tiktok.com/@a/video/1"},
        ),
        ("https://www.tiktok.com/@a", "/v1/tiktok/profiles/a", {}),
        ("https://www.instagram.com/reel/Cx1/", "/v2/instagram/media/Cx1", {}),
        ("https://www.instagram.com/nike/", "/v2/instagram/profiles/nike", {}),
        (
            "https://x.com/nasa/status/9",
            "/v1/twitter/tweets",
            {"url": "https://x.com/nasa/status/9"},
        ),
    ],
)
def test_socialfetch_routes(url: str, endpoint: str, params: dict[str, str]) -> None:
    assert _sf_route(url) == (endpoint, params)


async def test_sociavault_profile_envelope(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.sociavault.com/v1/scrape/twitter/profile?handle=nasa",
        match_headers={"x-api-key": "sk_live_t"},
        json={
            "success": True,
            "credits_used": 1,
            "endpoint": "twitter/profile",
            "data": {"handle": "nasa", "followers": 90000000},
        },
    )
    result = await SociaVaultEngine(api_key="sk_live_t").scrape("https://x.com/nasa")
    assert isinstance(result.social, Profile)
    assert (result.social.platform, result.social.handle, result.social.followers) == (
        "twitter",
        "nasa",
        90000000,
    )


async def test_socialfetch_not_found_escalates(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.socialfetch.dev/v1/twitter/profiles/ghost",
        json={"data": {"lookupStatus": "not_found"}, "meta": {"creditsCharged": 1}},
    )
    with pytest.raises(EngineError):
        await SocialFetchEngine(api_key="sfk_t").scrape("https://x.com/ghost")


async def test_socialfetch_found_profile(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.socialfetch.dev/v1/twitter/profiles/nasa",
        json={
            "data": {"lookupStatus": "found", "username": "nasa", "followers": 5},
            "meta": {"creditsCharged": 1},
        },
    )
    result = await SocialFetchEngine(api_key="sfk_t").scrape("https://x.com/nasa")
    assert isinstance(result.social, Profile)
    assert result.social.handle == "nasa"
