"""Tests for the Bright Data Web Unlocker engine (``brightdata_unlocker_sync``).

All HTTP is mocked with pytest-httpx — offline by default.
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines import get_engine
from scrapefold.engines.base import EngineError
from scrapefold.engines.brightdata import BrightDataUnlockerEngine, _adapt
from scrapefold.options import ScrapeOptions

_HTML = "<html><body><h1>Unlocked</h1><p>product page</p></body></html>"


def test_registered_with_alias() -> None:
    assert get_engine("brightdata_unlocker_sync") is BrightDataUnlockerEngine
    assert get_engine("brightdata") is BrightDataUnlockerEngine


def test_unavailable_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    assert BrightDataUnlockerEngine().is_available() is False
    assert BrightDataUnlockerEngine(api_key="k").is_available() is True


def test_adapter_maps_zone_country_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIGHTDATA_UNLOCKER_ZONE", "zone_env")
    body = _adapt(ScrapeOptions(country="DE"), "https://x.test/")
    assert body == {"zone": "zone_env", "url": "https://x.test/", "format": "json", "country": "de"}
    body = _adapt(
        ScrapeOptions(extra={"brightdata_zone": "z2", "brightdata_render": True}), "https://x/"
    )
    assert body["zone"] == "z2" and body["render"] == "true"
    monkeypatch.delenv("BRIGHTDATA_UNLOCKER_ZONE")
    assert _adapt(ScrapeOptions(), "https://x/")["zone"] == "web_unlocker1"


async def test_json_envelope_maps_to_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://api.brightdata.com/request",
        method="POST",
        json={"status_code": 200, "headers": {"Content-Type": "text/html"}, "body": _HTML},
    )
    result = await BrightDataUnlockerEngine(api_key="secret").scrape("https://shop.test/p/1")

    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["Authorization"] == "Bearer secret"
    assert json.loads(request.content)["url"] == "https://shop.test/p/1"
    assert result.html == _HTML and "Unlocked" in result.markdown
    assert result.status_code == 200
    assert result.meta["headers"] == {"content-type": "text/html"}
    assert result.cost_usd == 0.0015


async def test_target_status_is_preserved(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"status_code": 403, "headers": {}, "body": "<html>no</html>"})
    result = await BrightDataUnlockerEngine(api_key="k").scrape("https://shop.test/")
    assert result.status_code == 403  # detection escalates / reports, not a silent success


async def test_api_error_raises_with_brd_reason(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=401, headers={"x-brd-error": "zone not found"}, text="")
    with pytest.raises(EngineError, match="zone not found"):
        await BrightDataUnlockerEngine(api_key="k").scrape("https://shop.test/")
