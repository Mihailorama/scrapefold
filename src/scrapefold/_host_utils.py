"""Host-comparison utilities shared by the crawler and the requests engine.

Kept in a top-level private module to avoid a circular dependency between
``scrapefold.crawler.sitemap`` (which needs host checking) and
``scrapefold.engines.requests`` (which also needs host checking for redirect
scope enforcement).
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
import tldextract


def _is_invalid_location_error(exc: httpx.RemoteProtocolError) -> bool:
    """Return True if a RemoteProtocolError came from a malformed Location header.

    httpx's message for this case is stable across versions tested:
    'Invalid URL in location header: ...'. Any other RemoteProtocolError
    (server disconnects, malformed response bodies) returns False.
    """
    return "Invalid URL in location header" in str(exc)


def same_host(url: str, root: str, follow_subdomains: bool) -> bool:
    """Return True if *url* is on the same host as *root*.

    When *follow_subdomains* is ``False`` (the default), only an exact
    host match (www/apex-normalised) passes.  When ``True``, all subdomains
    of the same registered domain pass.

    Returns ``False`` for non-HTTP/HTTPS URLs and for unparseable inputs.
    """
    try:
        url_parsed = urlparse(url)
        root_parsed = urlparse(root)
    except Exception:
        return False

    if url_parsed.scheme not in ("http", "https"):
        return False

    if follow_subdomains:

        def _reg_domain(u: str) -> str:
            ext = tldextract.extract(u)
            return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain

        return _reg_domain(url) == _reg_domain(root)
    else:
        url_host = url_parsed.netloc.lower().removeprefix("www.")
        root_host = root_parsed.netloc.lower().removeprefix("www.")
        return url_host == root_host


__all__ = ["_is_invalid_location_error", "same_host"]
