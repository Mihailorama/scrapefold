"""Normalized social-media entities — a thin, best-effort layer over the many
vendor JSON envelopes returned by the social engines (``scrapecreators``,
``socialcrawl``, ``apify_actor``, …).

Each engine fills ``ScrapeResult.json`` with the vendor's *raw* payload, whose
shape varies per platform and per endpoint. This module maps that payload onto
a small, stable set of dataclasses — :class:`Profile`, :class:`Post`,
:class:`Comment` — so a caller can read ``post.like_count`` without caring
whether the vendor called it ``likeCount``, ``diggCount`` or ``favorite_count``.

Design notes:

- **Best-effort, never lossy.** Every normalized entity keeps the untouched
  vendor object under ``.raw``. Unknown fields stay reachable; normalization
  only *adds* a convenience view.
- **Pure module.** Data + functions, no I/O, no engine imports. Engines call
  :func:`normalize_social` and attach the result to ``ScrapeResult.social``.
- **Hint-driven.** ``platform`` and ``kind`` hints (derived by the engine from
  the endpoint it hit) steer extraction; when absent, ``kind`` is inferred from
  the payload's field signature.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

Kind = Literal["profile", "post", "comment"]

# ---------------------------------------------------------------------------
# Field-alias tables — every vendor spelling we map to one canonical field.
# Ordered most-specific-first within a tuple; the first present, non-null key
# wins.
# ---------------------------------------------------------------------------

_HANDLE = (
    "username",
    "handle",
    "screen_name",
    "screenName",
    "ownerUsername",
    "uniqueId",
    "publicIdentifier",
    "public_identifier",
    "nickname",
)
_FIRST_NAME = ("firstName", "first_name")
_LAST_NAME = ("lastName", "last_name")
_NAME = ("fullName", "full_name", "displayName", "display_name", "name", "title", "nickname")
_PROFILE_URL = ("profileUrl", "profile_url", "url", "link")
_BIO = ("biography", "bio", "description", "summary", "about", "signature")
_FOLLOWERS = (
    "followersCount",
    "followers_count",
    "followerCount",
    "follower_count",
    "followers",
    "fan_count",
    "subscriberCount",
    "subscribers",
    "subscribers_count",
    "participants_count",
    "members_count",
)
_FOLLOWING = ("followingCount", "following_count", "followsCount", "following")
_POSTS_COUNT = (
    "postsCount",
    "posts_count",
    "mediaCount",
    "media_count",
    "videoCount",
    "statuses_count",
    "tweetCount",
)
_VERIFIED = ("isVerified", "is_verified", "verified")

_POST_ID = ("postId", "post_id", "id", "pk", "shortcode", "tweetId", "videoId", "aweme_id")
_POST_URL = ("postUrl", "post_url", "permalink", "webVideoUrl", "url", "link")
_POST_TEXT = ("caption", "text", "full_text", "content", "message", "desc", "description", "title")
_CREATED_AT = (
    "created_at",
    "createdAt",
    "taken_at",
    "publishedAt",
    "createTime",
    "timestamp",
    "date",
)
_LIKES = (
    "likeCount",
    "likesCount",
    "like_count",
    "favorite_count",
    "favoriteCount",
    "diggCount",
    "reactionCount",
    "likes",
)
_COMMENTS = (
    "commentCount",
    "commentsCount",
    "comment_count",
    "replyCount",
    "reply_count",
    "comments",
)
_SHARES = (
    "shareCount",
    "sharesCount",
    "share_count",
    "retweetCount",
    "retweet_count",
    "repostCount",
    "shares",
)
_VIEWS = (
    "viewCount",
    "viewsCount",
    "view_count",
    "playCount",
    "play_count",
    "videoViewCount",
    "views",
)

_COMMENT_TEXT = ("text", "body", "content", "comment", "message")
_AUTHOR_NESTED = ("author", "user", "owner", "account", "channel")

# Media extraction.
_VIDEO_URL = ("videoUrl", "video_url", "webVideoUrl", "playAddr", "downloadAddr", "video")
# Post-level image keys — must NOT include bare ``url``/``src`` (at post level
# those mean the permalink, not an image).
_IMAGE_URL = (
    "displayUrl",
    "display_url",
    "imageUrl",
    "image_url",
    "image",
    "photoUrl",
    "pictureUrl",
    "media_url_https",
    "thumbnailUrl",
    "thumbnail",
    "coverUrl",
    "cover",
)
# Inside a media-object dict, ``url``/``src`` legitimately point at the asset.
_MEDIA_OBJ_IMAGE_URL = (*_IMAGE_URL, "url", "src")
# Keys whose value is a list of media items (carousels, galleries, attachments).
_MEDIA_CONTAINERS = (
    "media",
    "images",
    "photos",
    "videos",
    "mediaUrls",
    "media_urls",
    "attachments",
    "childPosts",
    "carousel",
    "sidecarItems",
)
_VIDEO_EXTS = (".mp4", ".mov", ".m3u8", ".webm", ".m4v")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")
_VIDEO_TYPE_WORDS = ("video", "reel", "clip", "gif", "animated")


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


MediaType = Literal["image", "video"]


@dataclass(frozen=True)
class Media:
    """A single image or video attached to a post."""

    url: str | None = None
    type: MediaType | None = None
    thumbnail: str | None = None


@dataclass(frozen=True)
class Author:
    """Light reference to whoever produced a post or comment."""

    platform: str | None = None
    handle: str | None = None
    name: str | None = None
    url: str | None = None
    verified: bool | None = None
    followers: int | None = None


@dataclass(frozen=True)
class Profile:
    """A social account / channel / page."""

    platform: str | None = None
    handle: str | None = None
    name: str | None = None
    url: str | None = None
    bio: str | None = None
    followers: int | None = None
    following: int | None = None
    posts_count: int | None = None
    verified: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Post:
    """A single post / tweet / video / reel."""

    platform: str | None = None
    id: str | None = None
    url: str | None = None
    text: str | None = None
    author: Author | None = None
    created_at: Any = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    view_count: int | None = None
    media: list[Media] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Comment:
    """A single comment / reply."""

    platform: str | None = None
    id: str | None = None
    text: str | None = None
    author: Author | None = None
    created_at: Any = None
    like_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


SocialEntity = Profile | Post | Comment


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _first(payload: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    """Return the first present, non-null value among ``aliases``."""
    for key in aliases:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _as_int(value: Any) -> int | None:
    """Coerce a count to ``int``: handles ``"1,234"``, ``"12K"``-free numerics, floats."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace(" ", "").strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


def _as_str(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _author(payload: dict[str, Any], platform: str | None) -> Author | None:
    """Build an :class:`Author` from a nested author object or flat owner fields."""
    nested = _first(payload, _AUTHOR_NESTED)
    src = nested if isinstance(nested, dict) else payload
    handle = _as_str(_first(src, _HANDLE))
    name = _full_name(src)
    url = _as_str(_first(src, _PROFILE_URL))
    if handle is None and name is None and url is None:
        return None
    return Author(
        platform=platform,
        handle=handle,
        name=name,
        url=url,
        verified=_as_bool(_first(src, _VERIFIED)),
        followers=_as_int(_first(src, _FOLLOWERS)),
    )


def _has_any(payload: dict[str, Any], aliases: tuple[str, ...]) -> bool:
    return any(payload.get(k) is not None for k in aliases)


def _guess_media_type(url: str) -> MediaType | None:
    """Infer image vs video from a URL's file extension; ``None`` if unknown."""
    low = url.lower().split("?", 1)[0]
    if low.endswith(_VIDEO_EXTS):
        return "video"
    if low.endswith(_IMAGE_EXTS):
        return "image"
    return None


def _parse_media_item(item: Any) -> Media | None:
    """Build a :class:`Media` from a string URL or a media-object dict."""
    if isinstance(item, str):
        url = item.strip()
        return Media(url=url, type=_guess_media_type(url)) if url else None
    if not isinstance(item, dict):
        return None
    video = _as_str(_first(item, _VIDEO_URL))
    image = _as_str(_first(item, _MEDIA_OBJ_IMAGE_URL))
    type_word = str(item.get("type", "")).lower()
    if video:
        return Media(url=video, type="video", thumbnail=image)
    if image:
        if any(word in type_word for word in _VIDEO_TYPE_WORDS):
            media_type: MediaType | None = "video"
        else:
            media_type = _guess_media_type(image) or ("image" if image else None)
        return Media(url=image, type=media_type)
    return None


def _collect_media(payload: dict[str, Any]) -> list[Media]:
    """Best-effort gather of a post's media: list containers first, else scalars.

    Carousels / galleries (``media``, ``images``, ``childPosts``, …) take
    precedence; a single-media post falls back to the post's own
    ``videoUrl`` / image fields. Results are de-duplicated by URL, order kept.
    """
    collected: list[Media] = []
    for key in _MEDIA_CONTAINERS:
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                parsed = _parse_media_item(item)
                if parsed is not None:
                    collected.append(parsed)

    if not collected:
        video = _as_str(_first(payload, _VIDEO_URL))
        image = _as_str(_first(payload, _IMAGE_URL))
        if video:
            collected.append(Media(url=video, type="video", thumbnail=image))
        elif image:
            collected.append(Media(url=image, type=_guess_media_type(image) or "image"))

    seen: set[str | None] = set()
    deduped: list[Media] = []
    for media in collected:
        if media.url in seen:
            continue
        seen.add(media.url)
        deduped.append(media)
    return deduped


def _infer_kind(payload: dict[str, Any]) -> Kind | None:
    """Guess the entity kind from which alias families are present."""
    post_signal = _has_any(payload, _POST_TEXT) or _has_any(
        payload, _LIKES + _COMMENTS + _SHARES + _VIEWS
    )
    profile_signal = _has_any(payload, _FOLLOWERS + _FOLLOWING + _POSTS_COUNT + _BIO)
    if post_signal and not profile_signal:
        return "post"
    if profile_signal and not post_signal:
        return "profile"
    if post_signal and profile_signal:
        # Mixed (e.g. a profile object embedding its latest post counts) —
        # follower/bio fields are the stronger account signal.
        return "profile"
    if _has_any(payload, _HANDLE):
        return "profile"
    return None


# ---------------------------------------------------------------------------
# Per-kind builders
# ---------------------------------------------------------------------------


def _full_name(payload: dict[str, Any]) -> str | None:
    """Resolve a display name, composing first + last when no single name field exists.

    Many APIs (LinkedIn actors, some person endpoints) split the name into
    ``firstName`` / ``lastName`` rather than a single ``name`` field.
    """
    name = _as_str(_first(payload, _NAME))
    if name is not None:
        return name
    first = _as_str(_first(payload, _FIRST_NAME))
    last = _as_str(_first(payload, _LAST_NAME))
    composed = " ".join(part for part in (first, last) if part)
    return composed or None


def _build_profile(payload: dict[str, Any], platform: str | None) -> Profile:
    return Profile(
        platform=platform,
        handle=_as_str(_first(payload, _HANDLE)),
        name=_full_name(payload),
        url=_as_str(_first(payload, _PROFILE_URL)),
        bio=_as_str(_first(payload, _BIO)),
        followers=_as_int(_first(payload, _FOLLOWERS)),
        following=_as_int(_first(payload, _FOLLOWING)),
        posts_count=_as_int(_first(payload, _POSTS_COUNT)),
        verified=_as_bool(_first(payload, _VERIFIED)),
        raw=payload,
    )


def _build_post(payload: dict[str, Any], platform: str | None) -> Post:
    return Post(
        platform=platform,
        id=_as_str(_first(payload, _POST_ID)),
        url=_as_str(_first(payload, _POST_URL)),
        text=_as_str(_first(payload, _POST_TEXT)),
        author=_author(payload, platform),
        created_at=_first(payload, _CREATED_AT),
        like_count=_as_int(_first(payload, _LIKES)),
        comment_count=_as_int(_first(payload, _COMMENTS)),
        share_count=_as_int(_first(payload, _SHARES)),
        view_count=_as_int(_first(payload, _VIEWS)),
        media=_collect_media(payload),
        raw=payload,
    )


def _build_comment(payload: dict[str, Any], platform: str | None) -> Comment:
    return Comment(
        platform=platform,
        id=_as_str(_first(payload, _POST_ID)),
        text=_as_str(_first(payload, _COMMENT_TEXT)),
        author=_author(payload, platform),
        created_at=_first(payload, _CREATED_AT),
        like_count=_as_int(_first(payload, _LIKES)),
        raw=payload,
    )


_BUILDERS: dict[Kind, Callable[[dict[str, Any], str | None], SocialEntity]] = {
    "profile": _build_profile,
    "post": _build_post,
    "comment": _build_comment,
}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

# Endpoint resource segment -> entity kind. Consumed by ``platform_kind``.
_RESOURCE_KIND: dict[str, Kind] = {
    "profile": "profile",
    "channel": "profile",
    "company": "profile",
    "user": "profile",
    "account": "profile",
    "post": "post",
    "posts": "post",
    "video": "post",
    "tweet": "post",
    "reel": "post",
    "photo": "post",
    "comments": "comment",
    "comment": "comment",
    "replies": "comment",
}


# Host -> canonical platform name. Covers the social networks / messengers that
# multi-platform vendors (Apify, LabelUp, …) span — so a normalized entity can
# be tagged even when the engine learns the platform only from the target URL.
_PLATFORM_HOSTS: tuple[tuple[str, str], ...] = (
    ("instagram.com", "instagram"),
    ("tiktok.com", "tiktok"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("twitter.com", "twitter"),
    ("x.com", "twitter"),
    ("facebook.com", "facebook"),
    ("fb.com", "facebook"),
    ("reddit.com", "reddit"),
    ("linkedin.com", "linkedin"),
    ("t.me", "telegram"),
    ("telegram.me", "telegram"),
    ("telegram.dog", "telegram"),
    ("vk.com", "vk"),
    ("vk.ru", "vk"),
    ("max.ru", "max"),
    ("ok.ru", "odnoklassniki"),
    ("dzen.ru", "dzen"),
    ("zen.yandex.ru", "dzen"),
    ("rutube.ru", "rutube"),
    ("twitch.tv", "twitch"),
    ("pinterest.com", "pinterest"),
    ("threads.net", "threads"),
    ("likee.video", "likee"),
    ("yappy.media", "yappy"),
)


def platform_for_url(url: str) -> str | None:
    """Best-effort canonical platform name from a URL's host, or ``None``."""
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")
    for needle, platform in _PLATFORM_HOSTS:
        if host == needle or host.endswith("." + needle):
            return platform
    return None


def platform_kind(endpoint: str) -> tuple[str | None, Kind | None]:
    """Parse a gateway endpoint path into ``(platform, kind)``.

    Handles the ``/[v1/]<platform>/<resource>[/...]`` convention shared by the
    Scrape Creators and SocialCrawl engines, e.g. ``"/v1/tiktok/video"`` ->
    ``("tiktok", "post")`` and ``"/instagram/profile"`` ->
    ``("instagram", "profile")``. Returns ``(None, None)`` when the path is
    too short to classify.
    """
    segments = [seg for seg in endpoint.split("/") if seg and seg != "v1"]
    if not segments:
        return None, None
    platform = segments[0]
    kind: Kind | None = None
    for seg in segments[1:]:
        if seg in _RESOURCE_KIND:
            kind = _RESOURCE_KIND[seg]
            break
    return platform, kind


def normalize_social(
    payload: object,
    *,
    platform: str | None = None,
    kind: Kind | None = None,
) -> SocialEntity | list[SocialEntity] | None:
    """Map a vendor JSON ``payload`` onto normalized social entities.

    - ``dict`` -> a single :class:`Profile` / :class:`Post` / :class:`Comment`,
      or ``None`` when the shape cannot be classified.
    - ``list`` -> a list of the entities that could be normalized (un-classifiable
      items are dropped). Returns ``None`` when nothing normalized.
    - A single-key envelope such as ``{"data": {...}}`` or ``{"data": [...]}``
      is unwrapped before classification.

    ``platform`` and ``kind`` are hints (typically derived by the engine from
    the endpoint it hit). When ``kind`` is ``None`` it is inferred per item.
    """
    if isinstance(payload, list):
        out: list[SocialEntity] = []
        for item in payload:
            one = normalize_social(item, platform=platform, kind=kind)
            if isinstance(one, list):
                out.extend(one)
            elif one is not None:
                out.append(one)
        return out or None

    if not isinstance(payload, dict):
        return None

    # Unwrap a common single-key envelope: {"data": {...}}, {"result": [...]}.
    if len(payload) == 1:
        only = next(iter(payload.values()))
        if isinstance(only, (dict, list)):
            return normalize_social(only, platform=platform, kind=kind)

    resolved = kind or _infer_kind(payload)
    if resolved is None:
        return None
    return _BUILDERS[resolved](payload, platform)


__all__ = [
    "Author",
    "Comment",
    "Kind",
    "Media",
    "MediaType",
    "Post",
    "Profile",
    "SocialEntity",
    "normalize_social",
    "platform_for_url",
    "platform_kind",
]
