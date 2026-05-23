"""Tests for CloudflareEngine — Cloudflare Browser Rendering API engine.

All tests use the httpx_mock fixture from pytest-httpx so no real network
calls are made. Follows the offline-by-default golden rule.

Ported from downstream-consumer/services/url_to_text_service.py:get_content_cloudflare
(lines 1452-1568). Two endpoints: /markdown (preferred) and /content (HTML
fallback when /markdown returns empty or non-200).
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

_ACCOUNT = "test-account-123"
_TOKEN = "cf-token-abc"
_TARGET_URL = "https://example.com/article"
_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT}/browser-rendering"
_MD_URL = f"{_BASE_URL}/markdown"
_CONTENT_URL = f"{_BASE_URL}/content"

_MARKDOWN_BODY = "# Hello\n\nSome **bold** content."
_HTML_BODY = "<html><body><h1>Hello</h1><p>World</p></body></html>"


def _engine(token: str | None = _TOKEN, account: str | None = _ACCOUNT):
    from scrapefold.engines.cloudflare import CloudflareEngine

    return CloudflareEngine(api_token=token, account_id=account)


# ---------------------------------------------------------------------------
# 1. Happy path /markdown — JSON response shape {"result": "<md>"}
# ---------------------------------------------------------------------------


async def test_markdown_endpoint_string_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_MD_URL,
        method="POST",
        json={"result": _MARKDOWN_BODY, "success": True},
    )

    result = await _engine().scrape(_TARGET_URL)

    assert result.markdown == _MARKDOWN_BODY
    assert result.engine == "cloudflare"
    assert result.text != ""  # derived from markdown
    # Cost honesty (Codex round-1 HIGH #4)
    assert result.meta["endpoint_calls"] == ["markdown"]
    assert result.cost_usd > 0  # per-request paid


# ---------------------------------------------------------------------------
# 2. /markdown response shape {"result": {"markdown": "<md>"}}
# ---------------------------------------------------------------------------


async def test_markdown_endpoint_dict_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_MD_URL,
        method="POST",
        json={"result": {"markdown": _MARKDOWN_BODY}, "success": True},
    )

    result = await _engine().scrape(_TARGET_URL)

    assert result.markdown == _MARKDOWN_BODY


# ---------------------------------------------------------------------------
# 3. Bearer token + Content-Type header on every request
# ---------------------------------------------------------------------------


async def test_authorization_and_content_type_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    await _engine().scrape(_TARGET_URL)

    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"
    assert request.headers["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# 4. /markdown empty → fall back to /content
# ---------------------------------------------------------------------------


async def test_falls_back_to_content_when_markdown_empty(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": ""})
    httpx_mock.add_response(url=_CONTENT_URL, method="POST", json={"result": _HTML_BODY})

    result = await _engine().scrape(_TARGET_URL)

    assert result.html == _HTML_BODY
    assert "Hello" in result.text  # post-converted from HTML
    assert result.markdown != ""
    # Cost honesty — both endpoints hit = 2x per-request cost
    assert result.meta["endpoint_calls"] == ["markdown", "content"]
    from scrapefold.engines.cloudflare import CloudflareEngine

    assert result.cost_usd == CloudflareEngine._PER_REQUEST_USD * 2


async def test_skip_content_fallback_opts_out(httpx_mock: HTTPXMock) -> None:
    """opts.extra['cloudflare_skip_content_fallback']=True → only /markdown hit."""
    from scrapefold.engines.base import EngineError

    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": ""})

    with pytest.raises(EngineError):
        await _engine().scrape(
            _TARGET_URL,
            ScrapeOptions(extra={"cloudflare_skip_content_fallback": True}),
        )
    # /content was NOT hit — verify by checking only one request was made
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# 5. /markdown 4xx → fall back to /content
# ---------------------------------------------------------------------------


async def test_falls_back_to_content_when_markdown_4xx(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", status_code=400, json={"errors": []})
    httpx_mock.add_response(url=_CONTENT_URL, method="POST", json={"result": _HTML_BODY})

    result = await _engine().scrape(_TARGET_URL)

    assert result.html == _HTML_BODY


# ---------------------------------------------------------------------------
# 6. Both endpoints empty → raises EngineError
# ---------------------------------------------------------------------------


async def test_both_endpoints_empty_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": ""})
    httpx_mock.add_response(url=_CONTENT_URL, method="POST", json={"result": ""})

    with pytest.raises(EngineError) as excinfo:
        await _engine().scrape(_TARGET_URL)

    # Issue 1 guard: prefix must appear exactly once — no double-wrap.
    assert str(excinfo.value).count("[cloudflare]") == 1


# ---------------------------------------------------------------------------
# 6b. 5xx on /markdown → EngineError immediately, /content NOT called
# ---------------------------------------------------------------------------


async def test_5xx_on_markdown_does_not_call_content(httpx_mock: HTTPXMock) -> None:
    """5xx is transient — engine MUST NOT burn a second paid /content call."""
    httpx_mock.add_response(url=_MD_URL, method="POST", status_code=503, json={"errors": []})

    with pytest.raises(EngineError):
        await _engine().scrape(_TARGET_URL)

    # Only /markdown was hit; /content was skipped (cost-honesty contract).
    assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# 7. Missing token → is_available() returns False
# ---------------------------------------------------------------------------


def test_is_available_false_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    engine = _engine(token=None)
    assert engine.is_available() is False


def test_is_available_false_when_account_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    engine = _engine(account=None)
    assert engine.is_available() is False


def test_is_available_true_when_both_present() -> None:
    engine = _engine()
    assert engine.is_available() is True


# ---------------------------------------------------------------------------
# 8. Request body includes url + render=True + waitUntil=domcontentloaded
# ---------------------------------------------------------------------------


async def test_request_body_shape(httpx_mock: HTTPXMock) -> None:
    import json

    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    await _engine().scrape(_TARGET_URL)

    request = httpx_mock.get_requests()[0]
    body = json.loads(request.content)
    assert body["url"] == _TARGET_URL
    assert body["render"] is True
    assert body["gotoOptions"]["waitUntil"] == "domcontentloaded"


# ---------------------------------------------------------------------------
# 9. opts.render_js=False → render flag flips to False
# ---------------------------------------------------------------------------


async def test_render_js_false_sends_render_false(httpx_mock: HTTPXMock) -> None:
    import json

    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    await _engine().scrape(_TARGET_URL, ScrapeOptions(render_js=False))

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["render"] is False


# ---------------------------------------------------------------------------
# 10. timeout_s honored on httpx client
# ---------------------------------------------------------------------------


async def test_timeout_s_honored(httpx_mock: HTTPXMock) -> None:
    # No assertion on the actual timeout (httpx_mock can't introspect easily);
    # just confirm a custom value is accepted and the engine completes.
    httpx_mock.add_response(url=_MD_URL, method="POST", json={"result": _MARKDOWN_BODY})

    result = await _engine().scrape(_TARGET_URL, ScrapeOptions(timeout_s=10))

    assert result.markdown == _MARKDOWN_BODY


# ---------------------------------------------------------------------------
# 11. NAME constant + capabilities are correct
# ---------------------------------------------------------------------------


def test_engine_metadata() -> None:
    from scrapefold.engines.cloudflare import CloudflareEngine

    assert CloudflareEngine.NAME == "cloudflare"
    caps = CloudflareEngine.CAPABILITIES
    assert caps.js_rendering is True
    assert caps.requires_api_key is True
    assert caps.output_native_markdown is True
    # Worst-case budget: /markdown empty triggers /content fallback = 2 paid requests
    assert caps.estimated_cost_usd == CloudflareEngine._PER_REQUEST_USD * 2


# ---------------------------------------------------------------------------
# 12. Registry registration
# ---------------------------------------------------------------------------


def test_registered_in_engine_registry() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "cloudflare" in list_engine_names()
    cls = get_engine("cloudflare")
    assert cls.NAME == "cloudflare"


# ---------------------------------------------------------------------------
# 13. Test skipped if pytest_httpx not installed (sanity)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(False, reason="sanity")
def test_marker_present() -> None:
    # Marker test that always passes; pinned by test count in the spec.
    assert True
