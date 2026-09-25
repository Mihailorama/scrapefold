"""Offline contract checks for the new fetch adapters."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines import get_engine
from scrapefold.options import ScrapeOptions


@pytest.mark.parametrize(
    ("name", "url", "payload"),
    [
        (
            "browserbase",
            "https://api.browserbase.com/v1/fetch",
            {"content": "<h1>Hello</h1>", "contentType": "text/html", "statusCode": 200},
        ),
        (
            "tinyfish",
            "https://api.fetch.tinyfish.ai",
            {
                "results": [{"url": "https://a.test", "text": "# Hello", "title": "Hello"}],
                "errors": [],
            },
        ),
        (
            "linkup",
            "https://api.linkup.so/v1/fetch",
            {"markdown": "# Hello", "rawContent": "<h1>Hello</h1>", "contentType": "html"},
        ),
        (
            "nimble",
            "https://sdk.nimbleway.com/v2/extract",
            {
                "status": "success",
                "url": "https://a.test",
                "data": {"html": "<h1>Hello</h1>", "markdown": "# Hello"},
            },
        ),
    ],
)
async def test_fetch_adapter(httpx_mock: HTTPXMock, name: str, url: str, payload: dict) -> None:
    httpx_mock.add_response(method="POST", url=url, json=payload)
    result = await get_engine(name)(api_key="test-key").scrape("https://a.test")
    assert result.markdown == "# Hello"
    assert result.text == "Hello"
    assert result.engine == name


async def test_tinyfish_json_and_nimble_stealth(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        method="POST",
        url="https://api.fetch.tinyfish.ai",
        json={"results": [{"text": {"heading": "Hello"}}], "errors": []},
    )
    result = await get_engine("tinyfish")("test-key").scrape(
        "https://a.test", ScrapeOptions(output_format="json")
    )
    assert result.json == {"heading": "Hello"}
    assert json.loads(result.text) == result.json

    httpx_mock.add_response(
        method="POST",
        url="https://sdk.nimbleway.com/v2/extract",
        json={"status": "success", "data": {"markdown": "# Hello"}},
    )
    await get_engine("nimble")("test-key").scrape(
        "https://a.test", ScrapeOptions(render_js=False, stealth=True)
    )
    assert json.loads(httpx_mock.get_requests()[-1].content)["render"] is True


async def test_browserbase_target_error_is_not_a_success(httpx_mock: HTTPXMock) -> None:
    from scrapefold.engines.base import EngineError

    httpx_mock.add_response(
        method="POST",
        url="https://api.browserbase.com/v1/fetch",
        json={"content": "not found", "contentType": "text/plain", "statusCode": 404},
    )
    with pytest.raises(EngineError, match="404"):
        await get_engine("browserbase")("test-key").scrape("https://a.test")


async def test_browserbase_fetch_contract(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scrapefold.engines.base import EngineError

    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    assert not get_engine("browserbase")().is_available()
    httpx_mock.add_response(
        method="POST", url="https://api.browserbase.com/v1/fetch", json={"content": ""}
    )
    with pytest.raises(EngineError, match="no page content"):
        await get_engine("browserbase")("test-key").scrape("https://a.test")
    request = httpx_mock.get_requests()[0]
    assert request.headers["X-BB-API-Key"] == "test-key"
    assert json.loads(request.content) == {"url": "https://a.test", "allowRedirects": True}
