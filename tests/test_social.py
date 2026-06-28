"""Tests for the normalized social-entity layer (scrapefold.social)."""

from __future__ import annotations

import pytest

from scrapefold.social import (
    Author,
    Comment,
    Media,
    Post,
    Profile,
    normalize_social,
    platform_for_url,
    platform_kind,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/nasa/", "instagram"),
        ("https://t.me/durov", "telegram"),
        ("https://vk.com/foo", "vk"),
        ("https://vk.ru/foo", "vk"),
        ("https://max.ru/u/foo", "max"),
        ("https://ok.ru/foo", "odnoklassniki"),
        ("https://www.twitch.tv/foo", "twitch"),
        ("https://x.com/foo", "twitter"),
        ("https://example.com/foo", None),
    ],
)
def test_platform_for_url(url: str, expected: str | None) -> None:
    assert platform_for_url(url) == expected


# ---------------------------------------------------------------------------
# platform_kind — endpoint parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("/v1/tiktok/video", ("tiktok", "post")),
        ("/instagram/profile", ("instagram", "profile")),
        ("/v1/youtube/channel", ("youtube", "profile")),
        ("/twitter/tweet", ("twitter", "post")),
        # First meaningful resource wins: this endpoint returns a post with its
        # comments nested, so the primary entity is the post.
        ("/reddit/post/comments", ("reddit", "post")),
        ("/youtube/video/comments", ("youtube", "post")),
        ("/instagram/comments", ("instagram", "comment")),
        ("/linkedin/company", ("linkedin", "profile")),
        ("/v1/tiktok", ("tiktok", None)),
        ("", (None, None)),
    ],
)
def test_platform_kind(endpoint: str, expected: tuple) -> None:
    assert platform_kind(endpoint) == expected


# ---------------------------------------------------------------------------
# Profile normalization + alias coverage
# ---------------------------------------------------------------------------


def test_profile_basic() -> None:
    payload = {
        "username": "natgeo",
        "fullName": "National Geographic",
        "biography": "Taking our followers around the world.",
        "followersCount": 280_000_000,
        "isVerified": True,
        "profileUrl": "https://instagram.com/natgeo",
    }
    entity = normalize_social(payload, platform="instagram", kind="profile")
    assert isinstance(entity, Profile)
    assert entity.handle == "natgeo"
    assert entity.name == "National Geographic"
    assert entity.followers == 280_000_000
    assert entity.verified is True
    assert entity.platform == "instagram"
    assert entity.raw is payload  # raw preserved


def test_profile_name_composed_from_first_last() -> None:
    # LinkedIn-style payload: name split into firstName / lastName, handle in
    # publicIdentifier, bio in summary.
    payload = {
        "firstName": "Satya",
        "lastName": "Nadella",
        "publicIdentifier": "satyanadella",
        "summary": "Empowering every person and organization.",
    }
    entity = normalize_social(payload, platform="linkedin", kind="profile")
    assert isinstance(entity, Profile)
    assert entity.name == "Satya Nadella"
    assert entity.handle == "satyanadella"
    assert entity.bio == "Empowering every person and organization."


@pytest.mark.parametrize(
    "follower_key",
    ["followersCount", "followers_count", "followerCount", "followers", "fan_count"],
)
def test_profile_follower_aliases(follower_key: str) -> None:
    entity = normalize_social({"handle": "x", follower_key: 123}, kind="profile")
    assert isinstance(entity, Profile)
    assert entity.followers == 123


# ---------------------------------------------------------------------------
# Post normalization
# ---------------------------------------------------------------------------


def test_post_tiktok_metrics() -> None:
    payload = {
        "id": "7123",
        "desc": "a dancing video",
        "diggCount": 50000,
        "commentCount": 1200,
        "shareCount": 900,
        "playCount": 1_000_000,
        "webVideoUrl": "https://tiktok.com/@u/video/7123",
        "author": {"uniqueId": "someuser", "nickname": "Some User", "verified": True},
    }
    entity = normalize_social(payload, platform="tiktok", kind="post")
    assert isinstance(entity, Post)
    assert entity.id == "7123"
    assert entity.text == "a dancing video"
    assert entity.like_count == 50000
    assert entity.comment_count == 1200
    assert entity.share_count == 900
    assert entity.view_count == 1_000_000
    assert entity.url == "https://tiktok.com/@u/video/7123"
    assert isinstance(entity.author, Author)
    assert entity.author.handle == "someuser"
    assert entity.author.verified is True


def test_post_string_counts_coerced_to_int() -> None:
    entity = normalize_social(
        {"caption": "hi", "likesCount": "1,234", "commentsCount": "56"},
        kind="post",
    )
    assert isinstance(entity, Post)
    assert entity.like_count == 1234
    assert entity.comment_count == 56


# ---------------------------------------------------------------------------
# Media extraction
# ---------------------------------------------------------------------------


def test_media_single_video_with_cover() -> None:
    payload = {
        "caption": "clip",
        "webVideoUrl": "https://cdn.tt/v/7.mp4",
        "coverUrl": "https://cdn.tt/c/7.jpg",
    }
    post = normalize_social(payload, kind="post")
    assert isinstance(post, Post)
    assert post.media == [
        Media(url="https://cdn.tt/v/7.mp4", type="video", thumbnail="https://cdn.tt/c/7.jpg")
    ]


def test_media_single_image() -> None:
    post = normalize_social({"caption": "pic", "displayUrl": "https://ig/p.jpg"}, kind="post")
    assert isinstance(post, Post)
    assert post.media == [Media(url="https://ig/p.jpg", type="image")]


def test_media_permalink_url_not_mistaken_for_image() -> None:
    # Post-level ``url`` is the permalink — it must NOT become media.
    post = normalize_social(
        {"caption": "text only", "url": "https://x.com/u/status/1", "likeCount": 3},
        kind="post",
    )
    assert isinstance(post, Post)
    assert post.url == "https://x.com/u/status/1"
    assert post.media == []


def test_media_carousel_list_of_dicts() -> None:
    payload = {
        "caption": "gallery",
        "images": [
            {"url": "https://ig/1.jpg", "type": "image"},
            {"url": "https://ig/2.jpg"},
            {"videoUrl": "https://ig/3.mp4", "displayUrl": "https://ig/3.jpg"},
        ],
    }
    post = normalize_social(payload, kind="post")
    assert isinstance(post, Post)
    assert [m.url for m in post.media] == [
        "https://ig/1.jpg",
        "https://ig/2.jpg",
        "https://ig/3.mp4",
    ]
    assert post.media[2].type == "video"
    assert post.media[2].thumbnail == "https://ig/3.jpg"


def test_media_list_of_strings_typed_by_extension() -> None:
    payload = {"caption": "g", "mediaUrls": ["https://a/x.mp4", "https://a/y.png", "https://a/z"]}
    post = normalize_social(payload, kind="post")
    assert isinstance(post, Post)
    assert [(m.url, m.type) for m in post.media] == [
        ("https://a/x.mp4", "video"),
        ("https://a/y.png", "image"),
        ("https://a/z", None),
    ]


def test_media_twitter_entities_with_type_word() -> None:
    payload = {
        "full_text": "tweet",
        "media": [{"media_url_https": "https://t/v.jpg", "type": "animated_gif"}],
    }
    post = normalize_social(payload, platform="twitter", kind="post")
    assert isinstance(post, Post)
    assert post.media[0].url == "https://t/v.jpg"
    assert post.media[0].type == "video"  # inferred from the "gif"/"animated" type word


def test_media_deduplicated_by_url() -> None:
    payload = {
        "caption": "dup",
        "images": [{"url": "https://a/1.jpg"}],
        "photos": [{"url": "https://a/1.jpg"}],
    }
    post = normalize_social(payload, kind="post")
    assert isinstance(post, Post)
    assert len(post.media) == 1


def test_media_absent_is_empty_list() -> None:
    post = normalize_social({"caption": "just words", "likeCount": 1}, kind="post")
    assert isinstance(post, Post)
    assert post.media == []


# ---------------------------------------------------------------------------
# Comment normalization
# ---------------------------------------------------------------------------


def test_comment_basic() -> None:
    payload = {"id": "c1", "body": "great post!", "likeCount": 7, "author": {"username": "fan"}}
    entity = normalize_social(payload, platform="reddit", kind="comment")
    assert isinstance(entity, Comment)
    assert entity.text == "great post!"
    assert entity.like_count == 7
    assert entity.author is not None
    assert entity.author.handle == "fan"


# ---------------------------------------------------------------------------
# kind inference (no hint)
# ---------------------------------------------------------------------------


def test_infer_post_from_engagement() -> None:
    entity = normalize_social({"text": "hello", "likeCount": 5})
    assert isinstance(entity, Post)


def test_infer_profile_from_followers() -> None:
    entity = normalize_social({"handle": "x", "followersCount": 10, "biography": "bio"})
    assert isinstance(entity, Profile)


def test_mixed_signal_prefers_profile() -> None:
    # A profile object that also embeds aggregate post counts.
    entity = normalize_social({"username": "x", "followersCount": 9, "likeCount": 3})
    assert isinstance(entity, Profile)


def test_unclassifiable_returns_none() -> None:
    assert normalize_social({"foo": "bar", "baz": 1}) is None
    assert normalize_social("not a dict") is None
    assert normalize_social(42) is None


# ---------------------------------------------------------------------------
# Envelope unwrap + list handling
# ---------------------------------------------------------------------------


def test_single_key_envelope_unwrapped() -> None:
    entity = normalize_social({"data": {"username": "x", "followersCount": 1}}, kind="profile")
    assert isinstance(entity, Profile)
    assert entity.handle == "x"


def test_list_payload_returns_list() -> None:
    payload = [
        {"caption": "first", "likeCount": 1},
        {"caption": "second", "likeCount": 2},
    ]
    result = normalize_social(payload, platform="instagram", kind="post")
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(p, Post) for p in result)
    assert result[0].text == "first"


def test_list_drops_unclassifiable_items() -> None:
    payload = [{"caption": "ok", "likeCount": 1}, {"foo": "bar"}]
    result = normalize_social(payload, kind=None)
    assert isinstance(result, list)
    assert len(result) == 1


def test_empty_list_returns_none() -> None:
    assert normalize_social([]) is None
    assert normalize_social([{"foo": "bar"}]) is None


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------


def test_public_reexports() -> None:
    import scrapefold

    assert scrapefold.normalize_social is normalize_social
    assert scrapefold.Post is Post
    assert scrapefold.Profile is Profile
    assert scrapefold.Comment is Comment
