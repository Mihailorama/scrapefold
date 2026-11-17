"""Tests for LurkAPIEngine (offline, pytest-httpx). Contract: engines/lurkapi.py docstring."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.lurkapi import LurkAPIEngine, _lurk_route
from scrapefold.options import ScrapeOptions
from scrapefold.social import Profile, platform_kind


@pytest.mark.parametrize(
    ("url", "endpoint", "params"),
    [
        (
            "https://www.tiktok.com/@a/video/1",
            "/v2/tiktok/video",
            {"url": "https://www.tiktok.com/@a/video/1"},
        ),
        ("https://www.instagram.com/nike/", "/v1/instagram/profile", {"username": "nike"}),
        (
            "https://www.reddit.com/r/x/comments/abc/t/",
            "/v1/reddit/post/comments",
            {"url": "https://www.reddit.com/r/x/comments/abc/t/"},
        ),
        ("https://x.com/nasa", "/v1/twitter/profile", {"handle": "nasa"}),
    ],
)
def test_routes(url: str, endpoint: str, params: dict[str, str]) -> None:
    assert _lurk_route(url) == (endpoint, params)


async def test_instagram_profile_envelope(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.lurkapi.com/v1/instagram/profile?username=nike",
        match_headers={"x-api-key": "lk_live_test"},
        json={
            "success": True,
            "credits_remaining": 99,
            "credits_charged": 1,
            "data": {"username": "nike", "full_name": "Nike", "follower_count": 290800000},
        },
    )
    result = await LurkAPIEngine(api_key="lk_live_test").scrape("https://www.instagram.com/nike/")

    assert result.engine == "lurkapi"
    assert result.meta["lurkapi_endpoint"] == "/v1/instagram/profile"
    assert isinstance(result.social, Profile)
    assert (result.social.platform, result.social.handle, result.social.followers) == (
        "instagram",
        "nike",
        290800000,
    )


async def test_forced_endpoint_uses_lurkapi_prefix(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.lurkapi.com/v1/threads/post?url=https%3A%2F%2Fwww.threads.net%2Fp%2F1",
        json={"success": True, "text": "hi"},
    )
    opts = ScrapeOptions(extra={"lurkapi_endpoint": "/v1/threads/post"})
    await LurkAPIEngine(api_key="k").scrape("https://www.threads.net/p/1", opts)


def test_missing_key_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LURKAPI_API_KEY", raising=False)
    assert LurkAPIEngine().is_available() is False


def test_platform_kind_skips_any_version() -> None:
    assert platform_kind("/v2/tiktok/video") == ("tiktok", "post")
    assert platform_kind("/v3/tiktok/profile/videos") == ("tiktok", "profile")
