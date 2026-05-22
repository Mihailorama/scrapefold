"""Tests for JinaEngine — Jina AI Reader-based engine.

All tests use the ``httpx_mock`` fixture from pytest-httpx so no real network
calls are made. Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import base64
import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MARKDOWN_BODY = "# Hello\n\nSome **bold** content.\n\nA [link](https://example.com)."
_HTML_BODY = "<html><head><title>Test</title></head><body><h1>Hello</h1><p>World</p></body></html>"
_JSON_BODY = {"title": "Test Page", "content": "Some text", "links": []}
_TEXT_BODY = "Hello plain world"
_TARGET_URL = "https://example.com/article"
_READER_URL = "https://r.jina.ai/https://example.com/article"


def _engine(api_key: str | None = None):
    from scrapefold.engines.jina import JinaEngine

    return JinaEngine(api_key=api_key)


# ---------------------------------------------------------------------------
# 1. Default call → GET to r.jina.ai, markdown returned and populated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_default_call_markdown_populated(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_READER_URL,
        status_code=200,
        text=_MARKDOWN_BODY,
    )
    result = await _engine().scrape(_TARGET_URL)

    assert result.markdown == _MARKDOWN_BODY
    assert result.engine == "jina"
    assert result.meta["status_code"] == 200
    # text should also be populated (derived from markdown)
    assert result.text != ""
    assert "Hello" in result.text


# ---------------------------------------------------------------------------
# 2. With API key → Authorization: Bearer <key> header is sent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_api_key_sends_authorization_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    await _engine(api_key="jina-test-key-abc").scrape(_TARGET_URL)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("authorization") == "Bearer jina-test-key-abc"


# ---------------------------------------------------------------------------
# 3. Without API key → no Authorization header, still works
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_api_key_no_authorization_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    await _engine(api_key=None).scrape(_TARGET_URL)

    request = httpx_mock.get_requests()[0]
    assert "authorization" not in request.headers


# ---------------------------------------------------------------------------
# 4. output_format="text" → X-Return-Format: text header sent, text populated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_output_format_text_sends_header_and_populates_text(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_TEXT_BODY)

    opts = ScrapeOptions(output_format="text")
    result = await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-return-format") == "text"
    assert result.text == _TEXT_BODY
    assert result.markdown == _TEXT_BODY
    assert result.html is None


# ---------------------------------------------------------------------------
# 5. output_format="html" → X-Return-Format: html, html populated,
#    text+markdown derived via html_to_both
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_output_format_html_populates_html_text_markdown(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_HTML_BODY)

    opts = ScrapeOptions(output_format="html")
    result = await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-return-format") == "html"
    assert result.html == _HTML_BODY
    assert "Hello" in result.text
    assert "Hello" in result.markdown


# ---------------------------------------------------------------------------
# 6. output_format="json" → X-Return-Format: json, json populated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_output_format_json_populates_json_field(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_READER_URL,
        status_code=200,
        headers={"content-type": "application/json"},
        text=json.dumps(_JSON_BODY),
    )

    opts = ScrapeOptions(output_format="json")
    result = await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-return-format") == "json"
    assert result.json == _JSON_BODY


# ---------------------------------------------------------------------------
# 7. take_screenshot=True → X-Return-Format: screenshot, screenshot_b64 populated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_take_screenshot_sends_header_and_populates_b64(
    httpx_mock: HTTPXMock,
) -> None:
    fake_bytes = b"\x89PNG\r\nfake image data"
    httpx_mock.add_response(
        url=_READER_URL,
        status_code=200,
        content=fake_bytes,
    )

    opts = ScrapeOptions(take_screenshot=True)
    result = await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-return-format") == "screenshot"
    assert result.screenshot_b64 is not None
    assert result.screenshot_b64 == base64.b64encode(fake_bytes).decode()
    # text and markdown are empty when screenshot
    assert result.text == ""
    assert result.markdown == ""
    assert result.html is None


# ---------------------------------------------------------------------------
# 8. language="ru" → Accept-Language: ru header
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_language_sets_accept_language_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(language="ru")
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("accept-language") == "ru"


# ---------------------------------------------------------------------------
# 9. extra["jina_engine"]="readability" → X-Engine: readability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extra_jina_engine_sets_x_engine_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(extra={"jina_engine": "readability"})
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-engine") == "readability"


# ---------------------------------------------------------------------------
# 10. extra["jina_no_cache"]=True → X-No-Cache: true
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extra_jina_no_cache_sets_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(extra={"jina_no_cache": True})
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-no-cache") == "true"


# ---------------------------------------------------------------------------
# 11. extra["jina_foo_bar"]="x" → X-Foo-Bar: x (snake-to-hyphen-kebab transform)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extra_generic_jina_prefix_snake_to_kebab(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(extra={"jina_foo_bar": "x"})
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-foo-bar") == "x"


# ---------------------------------------------------------------------------
# 12. is_available() returns True with and without api_key
# ---------------------------------------------------------------------------


def test_is_available_true_without_api_key() -> None:
    from scrapefold.engines.jina import JinaEngine

    assert JinaEngine(api_key=None).is_available() is True


def test_is_available_true_with_api_key() -> None:
    from scrapefold.engines.jina import JinaEngine

    assert JinaEngine(api_key="some-key").is_available() is True


# ---------------------------------------------------------------------------
# 13. output_format="auto" → no X-Return-Format header sent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_output_format_auto_omits_return_format_header(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(output_format="auto")
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert "x-return-format" not in request.headers


# ---------------------------------------------------------------------------
# 14. extra["jina_timeout"]=30 → X-Timeout: 30
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extra_jina_timeout_sets_x_timeout_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(extra={"jina_timeout": 30})
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-timeout") == "30"


# ---------------------------------------------------------------------------
# 15. extra["jina_with_links_summary"]=True → X-With-Links-Summary: true
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_extra_jina_with_links_summary_sets_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(extra={"jina_with_links_summary": True})
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-with-links-summary") == "true"


# ---------------------------------------------------------------------------
# 16. custom_headers merge — caller-provided wins
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_custom_headers_override(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_READER_URL, status_code=200, text=_MARKDOWN_BODY)

    opts = ScrapeOptions(custom_headers={"X-Custom": "myvalue"})
    await _engine().scrape(_TARGET_URL, opts)

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("x-custom") == "myvalue"


# ---------------------------------------------------------------------------
# 17. JINA_API_KEY env var is picked up when no explicit api_key
# ---------------------------------------------------------------------------


def test_env_api_key_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JINA_API_KEY", "env-key-xyz")
    # Re-import to get a fresh instance using the env var
    from scrapefold.engines.jina import JinaEngine

    eng = JinaEngine()
    assert eng.api_key == "env-key-xyz"


# ---------------------------------------------------------------------------
# 18. Network error → EngineError raised
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(
        _httpx.ConnectError("connection refused"),
        url=_READER_URL,
    )
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape(_TARGET_URL)

    assert exc_info.value.engine == "jina"
