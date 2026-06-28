"""Tests for the free TelegramEngine (t.me public-channel parser).

Offline — the t.me HTML preview is served via pytest-httpx, never the network.
"""

from __future__ import annotations

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.telegram import TelegramEngine, _fetch_url, _parse_count
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult
from scrapefold.social import Post

# Minimal but structurally faithful t.me/s/<channel> HTML: two messages, one
# with a photo, one with a video player + view count.
_FEED_HTML = """
<html><body>
<div class="tgme_channel_info_header_title">Durov's Channel</div>
<div class="tgme_widget_message" data-post="durov/151">
  <div class="tgme_widget_message_author"><a>Pavel Durov</a></div>
  <a class="tgme_widget_message_photo_wrap"
     style="background-image:url('https://cdn.tg/photo1.jpg')"></a>
  <div class="tgme_widget_message_text">First post text</div>
  <a class="tgme_widget_message_date" href="https://t.me/durov/151">
    <time datetime="2024-01-02T10:00:00+00:00">Jan 2</time></a>
  <span class="tgme_widget_message_views">1.2K</span>
</div>
<div class="tgme_widget_message" data-post="durov/152">
  <a class="tgme_widget_message_video_player">
    <i class="tgme_widget_message_video_thumb"
       style="background-image:url('https://cdn.tg/thumb2.jpg')"></i></a>
  <div class="tgme_widget_message_text">Second post with video</div>
  <a class="tgme_widget_message_date" href="https://t.me/durov/152">
    <time datetime="2024-01-03T12:00:00+00:00">Jan 3</time></a>
  <span class="tgme_widget_message_views">3.4M</span>
</div>
</body></html>
"""

_TEST_URL = "https://t.me/durov"


# ---------------------------------------------------------------------------
# URL rewriting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://t.me/durov", "https://t.me/s/durov"),
        ("https://t.me/s/durov", "https://t.me/s/durov"),
        ("https://t.me/durov/151", "https://t.me/durov/151?embed=1&mode=tme"),
        ("https://telegram.me/durov", "https://t.me/s/durov"),
    ],
)
def test_fetch_url_rewrite(url: str, expected: str) -> None:
    assert _fetch_url(url) == expected


# ---------------------------------------------------------------------------
# Count parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("1.2K", 1200), ("3.4M", 3_400_000), ("567", 567), ("12", 12), ("", None), (None, None)],
)
def test_parse_count(text: str | None, expected: int | None) -> None:
    assert _parse_count(text) == expected


# ---------------------------------------------------------------------------
# Channel feed -> list of normalized posts
# ---------------------------------------------------------------------------


async def test_channel_feed_parses_posts(httpx_mock) -> None:
    httpx_mock.add_response(url="https://t.me/s/durov", text=_FEED_HTML)

    engine = TelegramEngine()
    result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.engine == "telegram"
    assert isinstance(result.social, list)
    assert len(result.social) == 2

    first, second = result.social
    assert first.platform == "telegram"
    assert first.id == "151"
    assert first.text == "First post text"
    assert first.url == "https://t.me/durov/151"
    assert first.created_at == "2024-01-02T10:00:00+00:00"
    assert first.view_count == 1200
    assert first.author is not None
    assert first.author.handle == "durov"
    assert [m.url for m in first.media] == ["https://cdn.tg/photo1.jpg"]
    assert first.media[0].type == "image"

    assert second.view_count == 3_400_000
    assert second.media[0].url == "https://cdn.tg/thumb2.jpg"
    assert second.media[0].type == "video"

    # text/markdown and json are also populated.
    assert "First post text" in result.text
    assert "Second post with video" in result.markdown
    assert isinstance(result.json, list)
    assert result.meta["message_count"] == 2


# ---------------------------------------------------------------------------
# Single message URL -> single Post
# ---------------------------------------------------------------------------


async def test_single_message_returns_one_post(httpx_mock) -> None:
    single = """
    <div class="tgme_widget_message" data-post="durov/151">
      <div class="tgme_widget_message_text">Only one</div>
      <a class="tgme_widget_message_date" href="https://t.me/durov/151"></a>
    </div>
    """
    httpx_mock.add_response(url="https://t.me/durov/151?embed=1&mode=tme", text=single)

    engine = TelegramEngine()
    result = await engine.scrape("https://t.me/durov/151")

    assert isinstance(result.social, Post)
    assert result.social.text == "Only one"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def test_non_telegram_url_raises() -> None:
    engine = TelegramEngine()
    with pytest.raises(EngineError):
        await engine.scrape("https://example.com/foo")


async def test_no_messages_raises(httpx_mock) -> None:
    httpx_mock.add_response(url="https://t.me/s/empty", text="<html><body>nothing</body></html>")

    engine = TelegramEngine()
    with pytest.raises(EngineError):
        await engine.scrape("https://t.me/empty")


async def test_http_error_wrapped(httpx_mock) -> None:
    httpx_mock.add_response(url="https://t.me/s/gone", status_code=404)

    engine = TelegramEngine()
    with pytest.raises(EngineError):
        await engine.scrape("https://t.me/gone")


# ---------------------------------------------------------------------------
# Availability + registry
# ---------------------------------------------------------------------------


def test_is_available_no_key_required() -> None:
    # Free engine — available without any API key.
    assert TelegramEngine().is_available() is True


def test_registry_resolves_telegram() -> None:
    from scrapefold.engines import get_engine

    assert get_engine("telegram") is TelegramEngine


async def test_unsupported_options_dropped(httpx_mock) -> None:
    httpx_mock.add_response(url="https://t.me/s/durov", text=_FEED_HTML)
    engine = TelegramEngine()
    # stealth / render_js are not supported — must be dropped, not raise.
    result = await engine.scrape(_TEST_URL, ScrapeOptions(stealth=True, render_js=True))
    assert isinstance(result, ScrapeResult)
