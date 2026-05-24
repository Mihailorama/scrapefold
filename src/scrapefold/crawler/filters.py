"""URL filtering — pure functions used by the crawler before scraping.

Three responsibilities:
- ``is_crawlable(url)``: scheme / extension / well-known-path gate.
- ``strip_tracking_params(url)``: drop utm_*, fbclid, gclid query params.
- ``filter_urls(urls, root, follow_subdomains)``: same-host dedup + fragment/tracking strip + crawlable.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import tldextract

_NON_HTML_EXTS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".zip",
        ".tar",
        ".gz",
        ".mp4",
        ".mp3",
        ".wav",
        ".css",
        ".js",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".xml",  # sitemap.xml is fetched separately, not crawled as content
        ".rss",
    }
)

_AUTH_SHARE_SEGMENTS: frozenset[str] = frozenset(
    {"login", "signin", "signup", "register", "auth", "logout", "share"}
)

_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)
_TRACKING_PARAM_EXACT: frozenset[str] = frozenset(
    {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga", "yclid"}
)


def is_crawlable(url: str) -> bool:
    """Return True if *url* points at something the crawler should fetch.

    Rejects: non-http(s) schemes, empty/fragment-only, common binary
    extensions, and well-known auth/share path segments.
    """
    if not url or url.startswith("#"):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in _NON_HTML_EXTS):
        return False
    segments = [s for s in path_lower.split("/") if s]
    return not (segments and segments[0] in _AUTH_SHARE_SEGMENTS)


def strip_tracking_params(url: str) -> str:
    """Drop utm_*, fbclid, gclid, msclkid, etc. from the URL's query string."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.startswith(_TRACKING_PARAM_PREFIXES) and k not in _TRACKING_PARAM_EXACT
    ]
    new_query = urlencode(kept)
    return urlunparse(parsed._replace(query=new_query))


def _registered_domain(url: str) -> str:
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def filter_urls(
    urls: list[str],
    *,
    root: str,
    follow_subdomains: bool = False,
) -> list[str]:
    """Filter a URL list to same-host crawlable URLs with tracking stripped.

    Order-preserving; deduplicated; empties returned as an empty list.

    When *follow_subdomains* is ``False`` (default), only the root host and
    its www/apex equivalent are accepted — e.g. ``admin.example.com`` is
    excluded even though it shares the registered domain.  Set
    *follow_subdomains* to ``True`` to keep all subdomains of the same
    registered domain (original behaviour).
    """
    root_host = urlparse(root).netloc.lower().lstrip("www.") if not follow_subdomains else None
    root_domain = _registered_domain(root)

    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        if not is_crawlable(raw):
            continue
        if follow_subdomains:
            if _registered_domain(raw) != root_domain:
                continue
        else:
            url_host = urlparse(raw).netloc.lower().lstrip("www.")
            if url_host != root_host:
                continue
        cleaned = strip_tracking_params(raw)
        # Strip fragment so that /page#intro and /page#pricing deduplicate.
        cleaned = urlunparse(urlparse(cleaned)._replace(fragment=""))
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


__all__ = ["filter_urls", "is_crawlable", "strip_tracking_params"]
