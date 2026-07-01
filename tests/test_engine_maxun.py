"""Tests for MaxunEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.

Contract pinned here (getmaxun/maxun@master, 2026-06):
  - Auth: x-api-key: <api_key> header
  - Run:  POST {base}/api/robots/{id}/runs  (synchronous — returns completed run)
          body {"formats": [...], ...}
  - Duplicate: POST {base}/api/robots/{id}/duplicate  body {"targetUrl": ...}
  - Response: {"statusCode": 200, "messageCode": "success",
               "run": {"status": "success", "runId": ..., "data": {...},
                       "screenshots": [...]}}
"""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.maxun import MaxunEngine, _build_run_body, _resolve_formats
from scrapefold.options import ScrapeOptions

_BASE_URL = "http://localhost:8080"
_ROBOT_ID = "robot-123"
_RUN_ENDPOINT = f"{_BASE_URL}/api/robots/{_ROBOT_ID}/runs"

_API_KEY = "test-maxun-key-123"

_HTML = "<html><head><title>Maxun Test</title></head><body><h1>Hello</h1><p>World</p></body></html>"
_MARKDOWN = "# Hello\n\nWorld"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(api_key: str | None = _API_KEY) -> MaxunEngine:
    return MaxunEngine(api_key=api_key)


def _opts(**kwargs) -> ScrapeOptions:
    extra = kwargs.pop("extra", {})
    extra.setdefault("maxun_robot_id", _ROBOT_ID)
    return ScrapeOptions(extra=extra, **kwargs)


def _run_response(
    *,
    status: str = "success",
    markdown: str = _MARKDOWN,
    html: str = _HTML,
    list_data: object = None,
    text_data: object = None,
    summary: str | None = None,
    screenshots: list | None = None,
) -> dict:
    return {
        "statusCode": 200,
        "messageCode": "success",
        "run": {
            "id": "internal-1",
            "runId": "run-42",
            "status": status,
            "robotId": _ROBOT_ID,
            "data": {
                "textData": text_data or {},
                "listData": list_data or {},
                "crawlData": {},
                "searchData": {},
                "markdown": markdown,
                "html": html,
                "links": [],
                "summary": summary,
                "promptResult": None,
            },
            "screenshots": screenshots or [],
        },
    }


def _add_run_response(httpx_mock: HTTPXMock, **kwargs) -> None:
    httpx_mock.add_response(
        method="POST",
        url=_RUN_ENDPOINT,
        status_code=200,
        json=_run_response(**kwargs),
    )


# ---------------------------------------------------------------------------
# 1. Success path — all format slots populated from a completed run
# ---------------------------------------------------------------------------


async def test_success_populates_all_slots(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock)
    result = await _engine().scrape("https://example.com/", _opts())

    assert result.html == _HTML
    assert result.markdown == _MARKDOWN  # native markdown wins over converted
    assert "Hello" in result.text
    assert result.engine == "maxun"
    assert result.cost_usd == 0.0
    assert result.meta["robot_id"] == _ROBOT_ID
    assert result.meta["run_id"] == "run-42"


async def test_markdown_only_response(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock, html="")
    result = await _engine().scrape("https://example.com/", _opts())

    assert result.html is None
    assert result.markdown == _MARKDOWN
    assert "Hello" in result.text


# ---------------------------------------------------------------------------
# 2. Structured robot output → ScrapeResult.json
# ---------------------------------------------------------------------------


async def test_structured_output_fills_json(httpx_mock: HTTPXMock) -> None:
    items = [{"title": "Post 1", "price": "$5"}, {"title": "Post 2", "price": "$7"}]
    _add_run_response(httpx_mock, markdown="", html="", list_data=items)
    result = await _engine().scrape("https://example.com/", _opts())

    assert result.json == {"listData": items}
    assert "Post 1" in result.text  # text/markdown derived from structured data
    assert "Post 2" in result.markdown


# ---------------------------------------------------------------------------
# 3. Run finished but not successful → EngineError
# ---------------------------------------------------------------------------


async def test_failed_run_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock, status="failed")
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/", _opts())

    assert exc_info.value.engine == "maxun"
    assert "failed" in exc_info.value.message


async def test_empty_run_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock, markdown="", html="")
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/", _opts())

    assert "no data" in exc_info.value.message


# ---------------------------------------------------------------------------
# 4. Vendor error (4xx/5xx) → EngineError
# ---------------------------------------------------------------------------


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="POST", url=_RUN_ENDPOINT, status_code=403, json={})
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/", _opts())

    assert exc_info.value.engine == "maxun"


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/", _opts())

    assert exc_info.value.engine == "maxun"


# ---------------------------------------------------------------------------
# 5. Missing robot id → EngineError (router escalates), no HTTP call made
# ---------------------------------------------------------------------------


async def test_missing_robot_id_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/", ScrapeOptions())

    assert "maxun_robot_id" in exc_info.value.message
    assert not httpx_mock.get_requests()


# ---------------------------------------------------------------------------
# 6. Timeout — engine respects opts.timeout_s (explicit non-default value)
# ---------------------------------------------------------------------------


async def test_timeout_respects_opts(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    with pytest.raises(EngineError):
        await _engine().scrape("https://example.com/", _opts(timeout_s=1))

    request = httpx_mock.get_requests()[0]
    assert request.extensions["timeout"]["read"] == 1.0


async def test_default_timeout_widened_for_sync_runs(httpx_mock: HTTPXMock) -> None:
    """The generic 60 s default is too tight for a blocking robot run — the
    engine widens it to its own 300 s default."""
    _add_run_response(httpx_mock)
    await _engine().scrape("https://example.com/", _opts())

    request = httpx_mock.get_requests()[0]
    assert request.extensions["timeout"]["read"] == 300.0


# ---------------------------------------------------------------------------
# 7. Missing API key → is_available() False
# ---------------------------------------------------------------------------


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAXUN_API_KEY", raising=False)
    assert MaxunEngine(api_key=None).is_available() is False


# ---------------------------------------------------------------------------
# 8. Auth header + adapter assertions (unified opt → native param)
# ---------------------------------------------------------------------------


async def test_api_key_header_sent(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock)
    await _engine().scrape("https://example.com/", _opts())

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-api-key") == _API_KEY


async def test_take_screenshot_maps_to_formats(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock)
    await _engine().scrape("https://example.com/", _opts(take_screenshot=True))

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["formats"] == ["markdown", "html", "screenshot-visible"]


def test_resolve_formats_html_only() -> None:
    assert _resolve_formats(_opts(output_format="html")) == ["html"]


def test_resolve_formats_override_wins() -> None:
    opts = _opts(extra={"maxun_formats": ["markdown", "summary"]})
    assert _resolve_formats(opts) == ["markdown", "summary"]


def test_run_body_passthrough_strips_routing_keys() -> None:
    opts = _opts(
        extra={
            "maxun_promptInstructions": "extract prices",
            "maxun_base_url": "http://maxun.internal:8080",
            "maxun_duplicate_for_url": True,
            "firecrawl_foo": "bar",
        }
    )
    body = _build_run_body(opts)
    assert body["promptInstructions"] == "extract prices"
    assert "robot_id" not in body
    assert "base_url" not in body
    assert "duplicate_for_url" not in body
    assert "firecrawl_foo" not in body and "foo" not in body


# ---------------------------------------------------------------------------
# 9. Base URL resolution — env / ctor / extra override
# ---------------------------------------------------------------------------


async def test_base_url_from_extra_override(httpx_mock: HTTPXMock) -> None:
    alt = "http://maxun.internal:9999"
    httpx_mock.add_response(
        method="POST",
        url=f"{alt}/api/robots/{_ROBOT_ID}/runs",
        status_code=200,
        json=_run_response(),
    )
    result = await _engine().scrape(
        "https://example.com/", _opts(extra={"maxun_base_url": alt + "/"})
    )
    assert result.engine == "maxun"


def test_base_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAXUN_BASE_URL", "http://maxun.example:8080/")
    assert MaxunEngine(api_key="k").base_url == "http://maxun.example:8080"


# ---------------------------------------------------------------------------
# 10. duplicate_for_url — robot duplicated to target the scraped URL first
# ---------------------------------------------------------------------------


async def test_duplicate_for_url_runs_the_copy(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE_URL}/api/robots/{_ROBOT_ID}/duplicate",
        status_code=201,
        json={"statusCode": 201, "messageCode": "success", "robot": {"id": "robot-copy-7"}},
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{_BASE_URL}/api/robots/robot-copy-7/runs",
        status_code=200,
        json=_run_response(),
    )

    target = "https://example.com/other-page"
    result = await _engine().scrape(target, _opts(extra={"maxun_duplicate_for_url": True}))

    dup_request = httpx_mock.get_requests()[0]
    assert json.loads(dup_request.content) == {"targetUrl": target}
    assert result.meta["robot_id"] == "robot-copy-7"


# ---------------------------------------------------------------------------
# 11. Screenshots + summary land in meta; inline base64 fills screenshot_b64
# ---------------------------------------------------------------------------


async def test_screenshots_and_summary_in_meta(httpx_mock: HTTPXMock) -> None:
    shots = [{"data": "base64encodedimage==", "mimetype": "image/png"}]
    _add_run_response(httpx_mock, screenshots=shots, summary="A short page summary")
    result = await _engine().scrape("https://example.com/", _opts(take_screenshot=True))

    assert result.screenshot_b64 == "base64encodedimage=="
    assert result.meta["screenshots"] == shots
    assert result.meta["summary"] == "A short page summary"


async def test_url_reference_screenshots_stay_in_meta_only(httpx_mock: HTTPXMock) -> None:
    shots = ["https://minio.local/maxun-run-screenshots/run-42.png"]
    _add_run_response(httpx_mock, screenshots=shots)
    result = await _engine().scrape("https://example.com/", _opts())

    assert result.screenshot_b64 is None
    assert result.meta["screenshots"] == shots


# ---------------------------------------------------------------------------
# 12. Unsupported options are dropped silently (golden rule #2)
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped(httpx_mock: HTTPXMock) -> None:
    _add_run_response(httpx_mock)
    result = await _engine().scrape(
        "https://example.com/", _opts(stealth=True, country="ru", wait_ms=9000)
    )
    assert result.engine == "maxun"


# ---------------------------------------------------------------------------
# 13. Registry — engine resolvable by name
# ---------------------------------------------------------------------------


def test_registered_in_engine_registry() -> None:
    from scrapefold.engines import get_engine

    assert get_engine("maxun") is MaxunEngine
