"""Tests for SpyTrendEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.

Contract pinned here:
  - MCP endpoint: POST https://mcp.spytrend.com/mcp  (JSON-RPC 2.0)
  - Token endpoint: POST https://mcp.spytrend.com/oauth2/token (client_credentials)
  - Auth: Authorization: Bearer <token>
  - tools/call -> result.structuredContent | content[].text -> ScrapeResult.json
  - Generic pass-through: opts.extra['spytrend_tool'] + spytrend_args/flat extras
"""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.spytrend import SpyTrendEngine, _adapt, _result_payload
from scrapefold.options import ScrapeOptions

_MCP = "https://mcp.spytrend.com/mcp"
_TOKEN_URL = "https://mcp.spytrend.com/oauth2/token"
_TOKEN = "test-bearer-abc"

_STRUCTURED = {"ads": [{"id": "ad_1", "advertiser": "Acme", "days_active": 87}]}


def _engine(token: str | None = _TOKEN, **kw) -> SpyTrendEngine:
    return SpyTrendEngine(api_key=token, **kw)


def _search_opts() -> ScrapeOptions:
    return ScrapeOptions(
        extra={"spytrend_tool": "search_ads", "spytrend_args": {"platform": "facebook"}}
    )


def _mock_call(
    httpx_mock: HTTPXMock, *, result: dict | None = None, status_code: int = 200
) -> None:
    envelope = {"jsonrpc": "2.0", "id": 1, "result": result or {"structuredContent": _STRUCTURED}}
    httpx_mock.add_response(method="POST", url=_MCP, status_code=status_code, json=envelope)


# ---------------------------------------------------------------------------
# 1. Basic tools/call -> json + text/markdown; Bearer header set
# ---------------------------------------------------------------------------


async def test_basic_call_populates_json(httpx_mock: HTTPXMock) -> None:
    _mock_call(httpx_mock)
    result = await _engine().scrape("spytrend://search", _search_opts())

    assert result.json == _STRUCTURED
    assert result.html is None
    assert "Acme" in result.text
    assert result.markdown.startswith("```json")
    assert result.engine == "spytrend"
    assert result.meta["spytrend_tool"] == "search_ads"


async def test_bearer_header_and_jsonrpc_body(httpx_mock: HTTPXMock) -> None:
    _mock_call(httpx_mock)
    await _engine().scrape("spytrend://search", _search_opts())

    req = httpx_mock.get_requests(url=_MCP)[0]
    assert req.headers["Authorization"] == f"Bearer {_TOKEN}"
    body = json.loads(req.content)
    assert body["method"] == "tools/call"
    assert body["params"] == {"name": "search_ads", "arguments": {"platform": "facebook"}}


# ---------------------------------------------------------------------------
# 2. Adapter: tool required; flat extras merge into arguments
# ---------------------------------------------------------------------------


def test_adapt_requires_tool() -> None:
    with pytest.raises(ValueError, match="spytrend_tool"):
        _adapt(ScrapeOptions(extra={"spytrend_args": {"x": 1}}))


def test_adapt_merges_flat_extras_over_args() -> None:
    opts = ScrapeOptions(
        extra={"spytrend_tool": "get_ad", "spytrend_args": {"id": "a"}, "spytrend_id": "b"}
    )
    tool, args = _adapt(opts)
    assert tool == "get_ad"
    assert args == {"id": "b"}  # flat spytrend_id overrides spytrend_args


async def test_missing_tool_raises_engine_error() -> None:
    with pytest.raises(EngineError) as exc:
        await _engine().scrape("spytrend://x", ScrapeOptions())
    assert exc.value.engine == "spytrend"


# ---------------------------------------------------------------------------
# 3. OAuth client_credentials fetches a token when none is pre-set
# ---------------------------------------------------------------------------


async def test_client_credentials_fetches_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=_TOKEN_URL, json={"access_token": "fetched-tok", "token_type": "Bearer"}
    )
    _mock_call(httpx_mock)

    eng = SpyTrendEngine(api_key=None, client_id="cid", client_secret="sec")
    await eng.scrape("spytrend://search", _search_opts())

    token_req = httpx_mock.get_requests(url=_TOKEN_URL)[0]
    assert b"grant_type=client_credentials" in token_req.content
    call_req = httpx_mock.get_requests(url=_MCP)[0]
    assert call_req.headers["Authorization"] == "Bearer fetched-tok"


# ---------------------------------------------------------------------------
# 4. Result payload extraction: structuredContent | content[].text (JSON) | raw
# ---------------------------------------------------------------------------


def test_result_payload_prefers_structured() -> None:
    assert _result_payload({"structuredContent": _STRUCTURED, "content": []}) == _STRUCTURED


def test_result_payload_parses_json_text() -> None:
    result = {"content": [{"type": "text", "text": json.dumps(_STRUCTURED)}]}
    assert _result_payload(result) == _STRUCTURED


def test_result_payload_keeps_raw_text() -> None:
    result = {"content": [{"type": "text", "text": "not json"}]}
    assert _result_payload(result) == "not json"


async def test_sse_response_parsed(httpx_mock: HTTPXMock) -> None:
    envelope = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": _STRUCTURED}}
    httpx_mock.add_response(
        method="POST",
        url=_MCP,
        headers={"content-type": "text/event-stream"},
        content=f"event: message\ndata: {json.dumps(envelope)}\n\n".encode(),
    )
    result = await _engine().scrape("spytrend://search", _search_opts())
    assert result.json == _STRUCTURED


# ---------------------------------------------------------------------------
# 5. JSON-RPC error and tool isError -> EngineError
# ---------------------------------------------------------------------------


async def test_jsonrpc_error_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST", url=_MCP, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "nope"}}
    )
    with pytest.raises(EngineError) as exc:
        await _engine().scrape("spytrend://search", _search_opts())
    assert exc.value.engine == "spytrend"


async def test_tool_iserror_raises(httpx_mock: HTTPXMock) -> None:
    _mock_call(httpx_mock, result={"isError": True, "content": [{"type": "text", "text": "bad"}]})
    with pytest.raises(EngineError):
        await _engine().scrape("spytrend://search", _search_opts())


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_MCP, status_code=402, json={})
    with pytest.raises(EngineError) as exc:
        await _engine().scrape("spytrend://search", _search_opts())
    assert exc.value.engine == "spytrend"


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(EngineError):
        await _engine().scrape("spytrend://search", _search_opts())


async def test_timeout_respected(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    with pytest.raises(EngineError):
        await _engine().scrape("spytrend://search", _search_opts().with_updates(timeout_s=1))


# ---------------------------------------------------------------------------
# 6. is_available: token OR client_id+secret; else False. env fallbacks.
# ---------------------------------------------------------------------------


def test_is_available_with_token() -> None:
    assert _engine(token="tok").is_available() is True


def test_is_available_with_client_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPYTREND_TOKEN", raising=False)
    assert SpyTrendEngine(api_key=None, client_id="c", client_secret="s").is_available() is True


def test_is_available_false_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SPYTREND_TOKEN", "SPYTREND_CLIENT_ID", "SPYTREND_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert SpyTrendEngine(api_key=None).is_available() is False


def test_creds_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPYTREND_TOKEN", "env-tok")
    monkeypatch.setenv("SPYTREND_CLIENT_ID", "env-cid")
    monkeypatch.setenv("SPYTREND_CLIENT_SECRET", "env-sec")
    eng = SpyTrendEngine()
    assert eng.api_key == "env-tok"
    assert eng._client_id == "env-cid"
    assert eng._client_secret == "env-sec"


# ---------------------------------------------------------------------------
# 7. Registry wiring
# ---------------------------------------------------------------------------


def test_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "spytrend" in list_engine_names()
    assert get_engine("spytrend") is SpyTrendEngine
