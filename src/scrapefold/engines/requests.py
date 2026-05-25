"""RequestsEngine — pure async HTTP GET via httpx.

The simplest possible engine: no JS rendering, no stealth, no API key.
Free. Used as the first (cheapest) rung in nearly every ladder.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from scrapefold._host_utils import _is_invalid_location_error, same_host as _same_host
from scrapefold.engines.base import (
    EngineCapabilities,
    RedirectScopeViolation,
    ScrapeEngine,
)
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT = "scrapefold-requests/0.1"
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


async def _fetch_with_same_host_redirects(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    scope: dict[str, Any],
    max_hops: int = 5,
) -> httpx.Response:
    """Manually follow redirects, rejecting any off-host target.

    When a redirect target is not on the same host as *scope["root"]*,
    raises :class:`~scrapefold.engines.base.EngineError` so the router
    treats it like any other engine failure (escalation or abort).

    *scope* keys:
      - ``"root"``             — the anchor URL for host comparison.
      - ``"follow_subdomains"`` — forwarded to :func:`~scrapefold._host_utils.same_host`.
    """
    current = url
    root: str = scope["root"]
    follow_subdomains: bool = scope.get("follow_subdomains", False)

    for _ in range(max_hops):
        try:
            resp = await client.get(current, headers=headers)
        except httpx.RemoteProtocolError as exc:
            # httpx parses the Location header even with follow_redirects=False;
            # a malformed Location (e.g. unterminated IPv6 bracket) raises
            # RemoteProtocolError before we can inspect it ourselves.  Only
            # treat the malformed-Location case as a scope violation; transient
            # protocol errors (server disconnects, etc.) should fall through to
            # the normal escalation path.
            if _is_invalid_location_error(exc):
                raise RedirectScopeViolation(
                    engine="requests",
                    message=(
                        f"malformed redirect Location header rejected by same_host_redirect_scope "
                        f"(root={root!r} httpx_error={exc!s})"
                    ),
                    elapsed_ms=0,
                    target=current,
                ) from exc
            raise  # transient protocol failure — let the base class wrap as EngineError
        if resp.status_code not in _REDIRECT_STATUS:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        try:
            target = urljoin(current, location)
        except ValueError:
            raise RedirectScopeViolation(
                engine="requests",
                message=(
                    f"malformed redirect Location header rejected by same_host_redirect_scope "
                    f"(root={root!r} location={location!r})"
                ),
                elapsed_ms=0,
                target=location,
            ) from None
        if not _same_host(target, root, follow_subdomains):
            raise RedirectScopeViolation(
                engine="requests",
                message=(
                    f"redirect to off-host target rejected by same_host_redirect_scope "
                    f"(root={root!r} target={target!r})"
                ),
                elapsed_ms=0,
                target=target,
            )
        current = target

    # Exhausted hops — return whatever the last response was.
    return resp  # type: ignore[return-value]  # always assigned inside the loop


class RequestsEngine(ScrapeEngine):
    """Async HTTP GET engine backed by httpx.

    Supports plain HTML, JSON, and plain-text responses. Non-2xx responses
    are returned as ``ScrapeResult`` (with ``meta["status_code"]``) rather
    than raising, so the router's detection layer can decide whether to
    escalate.
    """

    NAME = "requests"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=False,
        screenshot=False,
        requires_api_key=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        proxy_type="none",
        free_tier=True,
        default_timeout_s=30,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "user_agent",
            "custom_headers",
            "cookies",
            "timeout_s",
            "extra",  # carries same_host_redirect_scope and other escape-hatch keys
        }
    )

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Fetch *url* with the given options and return a ``ScrapeResult``.

        When ``opts.extra["same_host_redirect_scope"]`` is set to a dict with
        keys ``"root"`` (str) and optionally ``"follow_subdomains"`` (bool),
        redirects are followed manually and any hop that leaves the declared
        host raises :class:`~scrapefold.engines.base.EngineError`.  This is
        the engine-level SSRF guard; the crawler's HEAD pre-flight is a
        cheap early-skip on top of it.
        """
        # Cookies travel via the httpx client; the rest go as headers.
        headers = build_target_headers(opts, include_cookies=False)
        headers.setdefault("User-Agent", _DEFAULT_USER_AGENT)

        scope = opts.extra.get("same_host_redirect_scope") if opts.extra else None

        async with httpx.AsyncClient(
            timeout=float(opts.timeout_s),
            follow_redirects=scope is None,  # manual loop when scope is set
            cookies=opts.cookies or {},
        ) as client:
            if scope is None:
                response = await client.get(url, headers=headers)
            else:
                response = await _fetch_with_same_host_redirects(client, url, headers, scope=scope)

        content_type = response.headers.get("content-type", "")
        ct_base = content_type.split(";")[0].strip().lower()

        text_out: str
        markdown_out: str
        html_out: str | None = None
        json_out: dict | list | None = None  # type: ignore[type-arg]

        if ct_base == "application/json":
            json_out = response.json()
            text_out = json.dumps(json_out, ensure_ascii=False, indent=2)
            markdown_out = text_out

        elif ct_base == "text/html" or (
            ct_base in ("text/plain", "") and "<html" in response.text[:512].lower()
        ):
            # Body-sniffed HTML covers servers that send text/plain or no content-type.
            raw_html = response.text
            html_out = raw_html
            text_out, markdown_out = html_to_both(raw_html, base_url=str(response.url))

        elif ct_base == "text/plain":
            text_out = response.text
            markdown_out = response.text

        else:
            # Binary or unknown content-type — leave text/markdown empty
            logger.debug(
                "engine=requests unknown content-type=%r for %s; text/markdown empty",
                content_type,
                url,
            )
            text_out = ""
            markdown_out = ""

        return ScrapeResult(
            url=str(response.url),
            text=text_out,
            markdown=markdown_out,
            html=html_out,
            json=json_out,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            meta={
                "status_code": response.status_code,
                "content_type": content_type,
                "final_url": response.url,
            },
        )


__all__ = ["RequestsEngine"]
