"""SpyTrendEngine — ad-intelligence over SpyTrend's MCP endpoint.

SpyTrend (https://spytrend.com) has scraped and AI-categorised the whole
Facebook and TikTok ad space (10M+ new creatives/day), links creatives to
advertisers and to the *webmasters* behind whole site networks, and tracks how
long each ad has been live and how often a creative is re-uploaded. It exposes
**no REST API** — the only programmatic surface is an MCP server
(``streamable-http`` transport, JSON-RPC). This engine wraps that surface's
plain-HTTP fallback (``POST /mcp`` with a Bearer token + ``tools/call``) so
SpyTrend fits the scrapefold engine contract like any other paid vendor.

Because SpyTrend is **query-based** (search ads by filters) rather than
``URL → markdown``, this is a *generic pass-through*: the caller names the tool
and its arguments via ``opts.extra``; the engine does not hardcode each tool's
schema (SpyTrend has ~11 tools that evolve). The target ``url`` is a placeholder
and is ignored unless a tool needs it as an argument.

Pinned contract (as of 2026-08):
  MCP endpoint : https://mcp.spytrend.com/mcp   (POST, JSON-RPC 2.0)
  Token endpoint: https://mcp.spytrend.com/oauth2/token  (client_credentials)
                  form fields: grant_type, scope=mcp:read,
                  audience=https://mcp.spytrend.com/mcp (REQUIRED — empty aud
                  => /mcp 401), client_id, client_secret. Tokens live 24h;
                  a 401 invalid_token triggers one refresh.
  Auth         : Authorization: Bearer <token>
  Accept       : application/json, text/event-stream  (server may reply SSE)
  Method call  : {"method":"tools/call","params":{"name":<tool>,"arguments":{…}}}
  Billing      : token-metered (Meta rows free; TikTok consumes tokens).

Tools (27; names only — arguments passed through verbatim): ``search_ads``,
``get_ad``, ``get_advertiser``, ``search_advertisers``, ``search_creatives``,
``get_creative``, ``find_similar_ads``, ``find_similar_creatives``,
``search_webmasters``, ``get_webmaster``, ``find_similar_webmasters``,
``search_hubs``, ``search_shops``, ``get_shop``, ``get_media``, ``get_trends``,
``get_usage``, ``list_favorites``, ``add_to_favorites``, plus ``admin_*``.
Note ``search_ads``/``search_creatives`` take ``source`` (``meta`` — default,
rows FREE; ``tiktok`` — PAID, 100 tokens/row, Pro plan).

Credentials (either works; env fallbacks in parens):
  - Pre-obtained Bearer token  → ``api_key`` / ``SPYTREND_TOKEN``.
  - OAuth client_credentials    → ``SPYTREND_CLIENT_ID`` + ``SPYTREND_CLIENT_SECRET``.

Invocation surface (via ``opts.extra``)
---------------------------------------
- ``spytrend_tool``  : tool name to call (required; else the engine raises so
  the router can escalate).
- ``spytrend_args``  : a dict passed as the tool's ``arguments`` verbatim.
- ``spytrend_<key>`` : any other prefixed extra becomes an argument (merged on
  top of ``spytrend_args``), for convenience when arguments are flat.

Example::

    opts = ScrapeOptions(extra={
        "spytrend_tool": "search_ads",
        "spytrend_args": {"platform": "facebook", "query": "keto gummies", "active": True},
    })
    await SpyTrendEngine().scrape("spytrend://search", opts)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import json_to_scrape_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_MCP_URL = "https://mcp.spytrend.com/mcp"
_TOKEN_URL = "https://mcp.spytrend.com/oauth2/token"
# ``audience`` is REQUIRED: omit it and the token is minted with an empty ``aud``,
# after which /mcp answers 401. ``scope`` is minted read-only. Both overridable
# via SPYTREND_AUDIENCE / SPYTREND_SCOPE.
_AUDIENCE = "https://mcp.spytrend.com/mcp"
_SCOPE = "mcp:read"


def _adapt(opts: ScrapeOptions) -> tuple[str, dict[str, Any]]:
    """Resolve ``(tool_name, arguments)`` from ``spytrend_*`` extras.

    Raises ``ValueError`` when no ``spytrend_tool`` is supplied — SpyTrend is
    query-based, so there is nothing to call without an explicit tool.
    """
    extra = strip_extra_prefix(opts.extra, "spytrend_")
    tool = extra.pop("tool", None)
    if not tool:
        raise ValueError(
            "spytrend needs opts.extra['spytrend_tool'] (e.g. 'search_ads'); "
            "it is query-based, not URL-based"
        )
    args = dict(extra.pop("args", {}) or {})
    # Any remaining flat spytrend_<key> extras merge on top of spytrend_args.
    args.update(extra)
    return str(tool), args


def _extract_jsonrpc(response: httpx.Response) -> dict[str, Any]:
    """Return the JSON-RPC object from a plain-JSON or SSE ``text/event-stream`` body."""
    if "text/event-stream" in response.headers.get("content-type", ""):
        # SSE frames: take the last ``data:`` line that parses as a JSON-RPC reply.
        last: dict[str, Any] | None = None
        for line in response.text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                last = obj
        if last is None:
            raise ValueError("spytrend returned an SSE stream with no JSON-RPC reply")
        return last
    return response.json()


class SpyTrendEngine(ScrapeEngine):
    """Ad-intelligence engine backed by SpyTrend's MCP (JSON-RPC) endpoint.

    Generic tool pass-through: ``opts.extra['spytrend_tool']`` picks the tool,
    ``opts.extra['spytrend_args']`` (and flat ``spytrend_<key>`` extras) carry
    its arguments. The structured tool result lands in ``ScrapeResult.json``.

    Auth is a pre-obtained Bearer token (``SPYTREND_TOKEN``) or OAuth
    ``client_credentials`` (``SPYTREND_CLIENT_ID`` + ``SPYTREND_CLIENT_SECRET``).
    ``is_available()`` is ``False`` when neither is configured.
    """

    NAME = "spytrend"
    CAPABILITIES = EngineCapabilities(
        js_rendering=False,
        stealth=True,
        requires_api_key=True,
        estimated_cost_usd=0.0,  # token-metered on SpyTrend side, not per-call USD
        billing_unit="call",
        proxy_type="none",
        site_classified=False,
        output_native_markdown=False,
        avg_response_mb_estimate=0.5,  # JSON payload
        bills_failed_attempts=False,  # Meta rows free; failed searches don't burn tokens
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "timeout_s", "extra"})

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(api_key or os.getenv("SPYTREND_TOKEN"))
        self._client_id = client_id or os.getenv("SPYTREND_CLIENT_ID")
        self._client_secret = client_secret or os.getenv("SPYTREND_CLIENT_SECRET")
        self._audience = os.getenv("SPYTREND_AUDIENCE", _AUDIENCE)
        self._scope = os.getenv("SPYTREND_SCOPE", _SCOPE)
        self._token: str | None = self.api_key  # cached bearer; may be refreshed via OAuth

    def is_available(self) -> bool:
        return bool(self.api_key) or bool(self._client_id and self._client_secret)

    async def _bearer(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        """Return a Bearer token, fetching one via client_credentials if needed.

        ``force`` discards a cached token (used for a single retry after a 401).
        """
        if self._token and not force:
            return self._token
        if not (self._client_id and self._client_secret):
            raise ValueError("spytrend: no token and no client_id/client_secret to obtain one")
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "scope": self._scope,
                "audience": self._audience,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise ValueError("spytrend token endpoint returned no access_token")
        self._token = str(token)
        return self._token

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        tool, arguments = _adapt(opts)
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        logger.debug("spytrend tool=%s args=%s", tool, arguments)

        async with httpx.AsyncClient(timeout=float(opts.timeout_s)) as client:
            response = await self._call(client, body, force_token=False)
            # One retry with a fresh token if the first call was rejected as unauthorised.
            if response.status_code == 401 and self._client_id and self._client_secret:
                response = await self._call(client, body, force_token=True)

        response.raise_for_status()
        envelope = _extract_jsonrpc(response)

        if "error" in envelope:
            raise ValueError(f"spytrend JSON-RPC error: {envelope['error']}")

        result = envelope.get("result", {})
        if result.get("isError"):
            raise ValueError(f"spytrend tool {tool!r} reported an error: {result.get('content')}")

        payload = _result_payload(result)
        text_out, markdown_out = json_to_scrape_text(payload)

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=None,
            engine=self.NAME,
            elapsed_ms=0,  # base class fills this in
            cost_usd=0.0,
            json=payload,
            meta={"status_code": response.status_code, "spytrend_tool": tool},
        )

    async def _call(
        self, client: httpx.AsyncClient, body: dict[str, Any], *, force_token: bool
    ) -> httpx.Response:
        token = await self._bearer(client, force=force_token)
        return await client.post(
            _MCP_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )


def _result_payload(result: dict[str, Any]) -> Any:
    """Extract the useful payload from an MCP ``tools/call`` result.

    Prefers ``structuredContent``; else unwraps ``content[].text`` (parsing JSON
    text when it parses, keeping the raw string otherwise). Falls back to the
    whole result object.
    """
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content")
    if isinstance(content, list):
        texts = [
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        ]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except (json.JSONDecodeError, TypeError):
                return texts[0]
        if texts:
            return texts
    return result


__all__ = ["SpyTrendEngine"]
