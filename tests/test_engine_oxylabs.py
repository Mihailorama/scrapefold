"""Tests for OxylabsEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.
"""

from __future__ import annotations

import base64
import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.oxylabs import OxylabsEngine, _adapt
from scrapefold.options import ScrapeOptions

_ENDPOINT = "https://realtime.oxylabs.io/v1/queries"

_HTML = (
    "<html><head><title>Oxylabs Test</title></head><body><h1>Hello</h1><p>World</p></body></html>"
)

_USERNAME = "test-user"
_PASSWORD = "test-pass"


def _engine(username: str = _USERNAME, password: str = _PASSWORD) -> OxylabsEngine:
    return OxylabsEngine(username=username, password=password)


def _results_response(content, status_code: int = 200, **extra) -> dict:
    """Build an Oxylabs realtime ``{"results": [...]}`` envelope."""
    result = {"content": content, "status_code": status_code, "url": "https://example.com/"}
    result.update(extra)
    return {"results": [result]}


def _sent_payload(httpx_mock: HTTPXMock) -> dict:
    return json.loads(httpx_mock.get_requests()[0].content)


# ---------------------------------------------------------------------------
# 1. Basic 200 HTML → html, text, markdown populated; cost + engine set
# ---------------------------------------------------------------------------


async def test_html_200_populates_all_slots(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    result = await _engine().scrape("https://example.com/")

    assert result.html == _HTML
    assert "Hello" in result.text
    assert "Hello" in result.markdown
    assert result.cost_usd == OxylabsEngine._PER_RESULT_USD
    assert result.engine == "oxylabs"


# ---------------------------------------------------------------------------
# 2. Target status_code from the result is stored in meta
# ---------------------------------------------------------------------------


async def test_meta_status_code_from_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML, status_code=200))
    result = await _engine().scrape("https://example.com/")

    assert result.meta["status_code"] == 200


# ---------------------------------------------------------------------------
# 3. Endpoint + POST + Basic auth header
# ---------------------------------------------------------------------------


async def test_uses_post_with_basic_auth(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape("https://example.com/")

    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    assert str(request.url) == _ENDPOINT
    expected = base64.b64encode(f"{_USERNAME}:{_PASSWORD}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"


# ---------------------------------------------------------------------------
# 4. payload always carries source=universal + the target url
# ---------------------------------------------------------------------------


async def test_payload_source_and_url(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    target = "https://target.example.com/page"
    await _engine().scrape(target)

    payload = _sent_payload(httpx_mock)
    assert payload["source"] == "universal"
    assert payload["url"] == target


# ---------------------------------------------------------------------------
# 5. render_js=True → render="html"
# ---------------------------------------------------------------------------


async def test_render_js_true_sends_render_html(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape("https://example.com/", ScrapeOptions(render_js=True))

    assert _sent_payload(httpx_mock).get("render") == "html"


# ---------------------------------------------------------------------------
# 6. render_js=False → no render key (plain HTTP fetch)
# ---------------------------------------------------------------------------


async def test_render_js_false_omits_render(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape("https://example.com/", ScrapeOptions(render_js=False))

    assert "render" not in _sent_payload(httpx_mock)


# ---------------------------------------------------------------------------
# 7. country → geo_location
# ---------------------------------------------------------------------------


async def test_country_maps_to_geo_location(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape("https://example.com/", ScrapeOptions(country="United States"))

    assert _sent_payload(httpx_mock).get("geo_location") == "United States"


# ---------------------------------------------------------------------------
# 8. language → Accept-Language inside context headers
# ---------------------------------------------------------------------------


async def test_language_maps_to_context_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape("https://example.com/", ScrapeOptions(language="ru"))

    context = _sent_payload(httpx_mock)["context"]
    headers = next(c["value"] for c in context if c["key"] == "headers")
    assert headers["Accept-Language"] == "ru"


# ---------------------------------------------------------------------------
# 9. user_agent → User-Agent inside context headers
# ---------------------------------------------------------------------------


async def test_user_agent_maps_to_context_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape("https://example.com/", ScrapeOptions(user_agent="MyBot/2.0"))

    context = _sent_payload(httpx_mock)["context"]
    headers = next(c["value"] for c in context if c["key"] == "headers")
    assert headers["User-Agent"] == "MyBot/2.0"


# ---------------------------------------------------------------------------
# 10. cookies dict → Cookie header inside context headers
# ---------------------------------------------------------------------------


async def test_cookies_become_context_cookie_header(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape(
        "https://example.com/", ScrapeOptions(cookies={"session": "abc", "token": "xyz"})
    )

    context = _sent_payload(httpx_mock)["context"]
    headers = next(c["value"] for c in context if c["key"] == "headers")
    assert "session=abc" in headers["Cookie"]
    assert "token=xyz" in headers["Cookie"]


# ---------------------------------------------------------------------------
# 11. take_screenshot=True → render="png"; screenshot_b64 set; no html
# ---------------------------------------------------------------------------


async def test_take_screenshot_sets_render_png_and_b64(httpx_mock: HTTPXMock) -> None:
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    httpx_mock.add_response(json=_results_response(b64))
    result = await _engine().scrape("https://example.com/", ScrapeOptions(take_screenshot=True))

    assert _sent_payload(httpx_mock).get("render") == "png"
    assert result.screenshot_b64 == b64
    assert result.html is None


# ---------------------------------------------------------------------------
# 12. Parsed (structured) content dict → json slot populated
# ---------------------------------------------------------------------------


async def test_parsed_content_dict_fills_json_slot(httpx_mock: HTTPXMock) -> None:
    parsed = {"title": "Acme", "price": 42}
    httpx_mock.add_response(json=_results_response(parsed))
    result = await _engine().scrape("https://example.com/")

    assert result.json == parsed
    assert result.html is None


# ---------------------------------------------------------------------------
# 13. extra["oxylabs_*"] forwarded as top-level payload keys
# ---------------------------------------------------------------------------


async def test_extra_oxylabs_prefix_forwarded_to_payload(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    await _engine().scrape(
        "https://example.com/",
        ScrapeOptions(extra={"oxylabs_source": "amazon_product", "oxylabs_parse": True}),
    )

    payload = _sent_payload(httpx_mock)
    # source override wins over the default "universal"
    assert payload["source"] == "amazon_product"
    assert payload["parse"] is True


# ---------------------------------------------------------------------------
# 14. job_id stored in meta
# ---------------------------------------------------------------------------


async def test_job_id_stored_in_meta(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML, job_id="7311761534"))
    result = await _engine().scrape("https://example.com/")

    assert result.meta.get("oxylabs_job_id") == "7311761534"


# ---------------------------------------------------------------------------
# 15. is_available()
# ---------------------------------------------------------------------------


def test_is_available_true_with_creds() -> None:
    assert _engine().is_available() is True


def test_is_available_false_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OXYLABS_USERNAME", raising=False)
    monkeypatch.delenv("OXYLABS_PASSWORD", raising=False)
    assert OxylabsEngine(username=None, password=None).is_available() is False
    # password without username is still unusable
    assert OxylabsEngine(username=None, password="p").is_available() is False


# ---------------------------------------------------------------------------
# 16. API 4xx → EngineError carrying the vendor message
# ---------------------------------------------------------------------------


async def test_api_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=401, json={"message": "Invalid credentials"})
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "oxylabs"
    assert "Invalid credentials" in exc_info.value.message


# ---------------------------------------------------------------------------
# 17. Empty results → EngineError
# ---------------------------------------------------------------------------


async def test_empty_results_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"results": []})
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "oxylabs"


# ---------------------------------------------------------------------------
# 18. Network error → EngineError
# ---------------------------------------------------------------------------


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/")

    assert exc_info.value.engine == "oxylabs"


# ---------------------------------------------------------------------------
# 19. _adapt unit: non-oxylabs extra keys are not forwarded
# ---------------------------------------------------------------------------


def test_adapt_ignores_non_oxylabs_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    payload = _adapt(opts, "https://x.com")
    assert "firecrawl_foo" not in payload
    assert "unrelated" not in payload
    assert "foo" not in payload


# ---------------------------------------------------------------------------
# 20. Unsupported options silently dropped (no error)
# ---------------------------------------------------------------------------


async def test_unsupported_options_do_not_break_the_call(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_results_response(_HTML))
    # `premium_proxy` is not in SUPPORTED_OPTIONS; the base class drops it.
    result = await _engine().scrape("https://example.com/", ScrapeOptions(premium_proxy=True))
    assert result.engine == "oxylabs"
