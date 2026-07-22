"""TelegramEngine — free scraper for public Telegram channels via t.me previews.

Telegram exposes every public channel as server-rendered HTML at
``https://t.me/s/<channel>`` (the channel feed) and each message as an embed at
``https://t.me/<channel>/<id>?embed=1``. No API key, no login — this engine
fetches that HTML over plain ``httpx`` and parses the ``tgme_widget_message``
blocks into normalized :class:`~scrapefold.social.Post` objects (text, media,
view count, timestamp, author), populating ``ScrapeResult.social``.

URL handling
------------
============================  ===========================================
Target URL                     Fetched
============================  ===========================================
t.me/s/<channel>               as-is (channel feed, many messages)
t.me/<channel>                 rewritten to t.me/s/<channel>
t.me/<channel>/<id>            rewritten to t.me/<channel>/<id>?embed=1
============================  ===========================================

A non-Telegram URL, or a Telegram URL with no parseable messages, raises
``ValueError`` (wrapped as ``EngineError``) so the router escalates.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.options import ScrapeOptions, build_target_headers
from scrapefold.result import ScrapeResult
from scrapefold.social import Author, Media, Post

logger = logging.getLogger(__name__)

_TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog")
_BG_URL_RE = re.compile(r"url\(['\"]?(.*?)['\"]?\)")
_COUNT_RE = re.compile(r"^([\d.,]+)\s*([KkMm]?)")


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def _is_telegram(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _TELEGRAM_HOSTS)


def _fetch_url(url: str) -> str:
    """Rewrite a public Telegram URL to the HTML-preview form to fetch."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        raise ValueError(f"telegram: no channel in URL {url!r}")
    if segments[0] == "s":
        return f"https://t.me/{'/'.join(segments)}"  # already a feed preview
    if len(segments) == 1:
        return f"https://t.me/s/{segments[0]}"  # channel feed
    # /<channel>/<id> -> single-message embed
    return f"https://t.me/{segments[0]}/{segments[1]}?embed=1&mode=tme"


def _bg_image(style: str | None) -> str | None:
    if not style:
        return None
    match = _BG_URL_RE.search(style)
    return match.group(1) if match else None


def _parse_count(text: str | None) -> int | None:
    """Parse a Telegram view count like ``"1.2K"`` / ``"3.4M"`` / ``"567"``."""
    if not text:
        return None
    match = _COUNT_RE.match(text.strip())
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    suffix = match.group(2).lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


def _message_media(msg: Tag) -> list[Media]:
    media: list[Media] = []
    for photo in msg.select(".tgme_widget_message_photo_wrap"):
        url = _bg_image(photo.get("style"))  # type: ignore[arg-type]
        if url:
            media.append(Media(url=url, type="image"))
    for video in msg.select("video.tgme_widget_message_video"):
        src = video.get("src")
        if isinstance(src, str) and src:
            media.append(Media(url=src, type="video"))
    for player in msg.select("a.tgme_widget_message_video_player"):
        thumb_el = player.select_one(".tgme_widget_message_video_thumb")
        thumb = _bg_image(thumb_el.get("style")) if isinstance(thumb_el, Tag) else None  # type: ignore[arg-type]
        if thumb:
            media.append(Media(url=thumb, type="video"))
    # De-dup by URL, keep order.
    seen: set[str | None] = set()
    out: list[Media] = []
    for item in media:
        if item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


def _parse_messages(html: str) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")

    owner_el = soup.select_one(".tgme_channel_info_header_title, .tgme_header_title")
    owner_name = owner_el.get_text(strip=True) if isinstance(owner_el, Tag) else None

    posts: list[Post] = []
    for msg in soup.select("div.tgme_widget_message"):
        if not isinstance(msg, Tag):
            continue
        data_post = msg.get("data-post")
        data_post = data_post if isinstance(data_post, str) else None
        handle = data_post.split("/")[0] if data_post else None
        post_id = data_post.split("/")[-1] if data_post else None

        text_el = msg.select_one(".tgme_widget_message_text")
        text = text_el.get_text("\n", strip=True) if isinstance(text_el, Tag) else None

        date_el = msg.select_one("a.tgme_widget_message_date")
        href = date_el.get("href") if isinstance(date_el, Tag) else None
        post_url = (
            href if isinstance(href, str) else (f"https://t.me/{data_post}" if data_post else None)
        )

        time_el = msg.select_one("time[datetime]")
        created = time_el.get("datetime") if isinstance(time_el, Tag) else None

        views_el = msg.select_one(".tgme_widget_message_views")
        views = _parse_count(views_el.get_text(strip=True)) if isinstance(views_el, Tag) else None

        author_el = msg.select_one(".tgme_widget_message_author")
        author_name = author_el.get_text(strip=True) if isinstance(author_el, Tag) else owner_name
        author = (
            Author(
                platform="telegram",
                handle=handle,
                name=author_name,
                url=f"https://t.me/{handle}" if handle else None,
            )
            if (handle or author_name)
            else None
        )

        raw = {
            "id": post_id,
            "channel": handle,
            "url": post_url,
            "text": text,
            "datetime": created,
            "views": views,
            "media": [{"url": m.url, "type": m.type} for m in _message_media(msg)],
        }

        posts.append(
            Post(
                platform="telegram",
                id=post_id,
                url=post_url if isinstance(post_url, str) else None,
                text=text,
                author=author,
                created_at=created,
                view_count=views,
                media=_message_media(msg),
                raw=raw,
            )
        )
    return posts


class TelegramEngine(ScrapeEngine):
    """Free public-Telegram-channel scraper backed by t.me HTML previews.

    No API key required. Maps a public channel / message URL to its t.me
    preview, parses the ``tgme_widget_message`` blocks, and returns normalized
    :class:`~scrapefold.social.Post` objects under ``ScrapeResult.social`` (a
    single ``Post`` for a message URL, a list for a channel feed). The parsed
    structure is also exposed as ``ScrapeResult.json``.
    """

    NAME = "telegram"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=False,
        requires_api_key=False,
        free_tier=True,
        estimated_cost_usd=0.0,
        billing_unit="call",
        proxy_type="none",
        site_classified=True,
        output_native_markdown=False,
        avg_response_mb_estimate=0.5,  # JSON payload
    )
    SUPPORTED_OPTIONS = frozenset(
        {"language", "user_agent", "custom_headers", "timeout_s", "extra"}
    )

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        if not _is_telegram(url):
            raise ValueError(f"telegram engine only handles t.me URLs, got {url!r}")

        fetch_url = _fetch_url(url)
        headers = build_target_headers(opts, include_cookies=False)
        headers.setdefault("User-Agent", "Mozilla/5.0 (compatible; scrapefold/0.2)")

        async with httpx.AsyncClient(
            timeout=float(opts.timeout_s), follow_redirects=True
        ) as client:
            response = await client.get(fetch_url, headers=headers)
        response.raise_for_status()

        posts = _parse_messages(response.text)
        if not posts:
            raise ValueError(f"telegram: no messages parsed from {fetch_url!r}")

        # text/markdown: the message bodies, newest-last as Telegram renders them.
        bodies = [p.text for p in posts if p.text]
        text_out = "\n\n".join(bodies)
        markdown_out = "\n\n---\n\n".join(bodies)

        # A single-message URL yields one Post; a channel feed yields the list.
        social: Post | list[Post] = posts[0] if len(posts) == 1 else posts
        json_out = [p.raw for p in posts]

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=response.text,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            json=json_out,
            social=social,
            meta={
                "status_code": response.status_code,
                "telegram_fetch_url": fetch_url,
                "message_count": len(posts),
            },
        )


__all__ = ["TelegramEngine"]
