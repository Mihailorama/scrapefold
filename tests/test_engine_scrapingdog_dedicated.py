"""Tests for ScrapingdogDedicatedEngine (SCRAPEFOLD-20).

Recorded-response style: ``httpx_mock`` returns payloads shaped like
Scrapingdog's documented JSON responses; no network. The request parameters
are pinned to what ai-utils sends (``get_content_scrapingdog``).
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines import get_engine
from scrapefold.engines.base import EngineError
from scrapefold.engines.scrapingdog_dedicated import ScrapingdogDedicatedEngine, _route_for
from scrapefold.ladders import get_ladder, step_engines
from scrapefold.options import ScrapeOptions

_KEY = "local-dev-scrapingdog"

_PROFILE = [
    {
        "fullName": "John Doe",
        "public_identifier": "john-doe",
        "headline": "Engineer at Acme",
        "location": "Berlin",
        "experience": [{"position": "Engineer", "company_name": "Acme"}],
    }
]
_COMPANY = [{"company_name": "Acme", "universal_name_id": "acme", "industries": "Software"}]
_JOB = [{"job_position": "Data Engineer", "company_name": "Acme", "job_location": "Remote"}]
_PRODUCT = {
    "title": "Example Widget",
    "price": "$29.99",
    "average_rating": 4.5,
    "product_information": {"ASIN": "B07XYZ1234"},
}
_TWEET = [{"tweet_id": "1655608985058267139", "text": "hello", "likes": 10}]


def _engine() -> ScrapingdogDedicatedEngine:
    return ScrapingdogDedicatedEngine(api_key=_KEY)


@pytest.mark.parametrize(
    ("url", "endpoint", "params"),
    [
        (
            "https://www.linkedin.com/in/john-doe/",
            "/linkedin",
            {"type": "profile", "linkId": "john-doe", "private": "true"},
        ),
        (
            "https://www.linkedin.com/in/john-doe?trk=abc",
            "/linkedin",
            {"type": "profile", "linkId": "john-doe", "private": "true"},
        ),
        (
            "https://www.linkedin.com/company/acme/about/",
            "/linkedin",
            {"type": "company", "linkId": "acme"},
        ),
        (
            "https://www.linkedin.com/jobs/view/3812345678/",
            "/linkedinjobs",
            {"job_id": "3812345678"},
        ),
        (
            "https://www.linkedin.com/jobs/view/data-engineer-at-acme-3812345678",
            "/linkedinjobs",
            {"job_id": "3812345678"},
        ),
        (
            "https://www.linkedin.com/jobs/search/?currentJobId=3812345678",
            "/linkedinjobs",
            {"job_id": "3812345678"},
        ),
        (
            "https://www.amazon.com/dp/B07XYZ1234",
            "/amazon/product",
            {"domain": "com", "asin": "B07XYZ1234"},
        ),
        (
            "https://www.amazon.co.uk/Example-Widget/dp/B07XYZ1234/ref=sr_1_1",
            "/amazon/product",
            {"domain": "co.uk", "asin": "B07XYZ1234"},
        ),
        (
            "https://www.amazon.de/gp/product/B07XYZ1234",
            "/amazon/product",
            {"domain": "de", "asin": "B07XYZ1234"},
        ),
        (
            "https://twitter.com/elonmusk/status/1655608985058267139",
            "/twitter",
            {"url": "https://twitter.com/elonmusk/status/1655608985058267139", "parsed": "true"},
        ),
        ("https://x.com/elonmusk", "/twitter", {"url": "https://x.com/elonmusk", "parsed": "true"}),
    ],
)
def test_route_matches_ai_utils_params(url: str, endpoint: str, params: dict[str, str]) -> None:
    got_endpoint, got_params, _ = _route_for(url)
    assert got_endpoint == endpoint
    assert got_params == params


@pytest.mark.parametrize(
    "url",
    ["https://example.com/", "https://www.linkedin.com/jobs/", "https://www.amazon.com/s?k=foo"],
)
def test_unroutable_url_raises(url: str) -> None:
    with pytest.raises(ValueError):
        _route_for(url)


@pytest.mark.parametrize(
    ("url", "payload"),
    [
        ("https://www.linkedin.com/in/john-doe/", _PROFILE),
        ("https://www.linkedin.com/company/acme/", _COMPANY),
        ("https://www.linkedin.com/jobs/view/3812345678/", _JOB),
        ("https://www.amazon.com/dp/B07XYZ1234", _PRODUCT),
        ("https://twitter.com/elonmusk/status/1655608985058267139", _TWEET),
    ],
)
async def test_json_kept_as_json_and_rendered(
    httpx_mock: HTTPXMock, url: str, payload: object
) -> None:
    httpx_mock.add_response(json=payload)

    result = await _engine().scrape(url)

    assert result.json == payload
    assert result.html is None
    assert result.markdown.startswith("```json\n")
    assert json.loads(result.text) == payload
    assert result.engine == "scrapingdog_dedicated"
    assert result.status_code == 200
    request = httpx_mock.get_requests()[0]
    assert request.url.scheme == "https"
    assert request.url.host == "api.scrapingdog.com"
    assert request.url.params["api_key"] == _KEY


async def test_linkedin_profile_request_and_social(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_PROFILE)

    result = await _engine().scrape("https://www.linkedin.com/in/john-doe/")

    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/linkedin"
    assert dict(request.url.params) == {
        "api_key": _KEY,
        "type": "profile",
        "linkId": "john-doe",
        "private": "true",
    }
    assert result.meta["scrapingdog_endpoint"] == "/linkedin"


async def test_extra_overrides_params(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_PROFILE)
    opts = ScrapeOptions(extra={"scrapingdog_dedicated_private": "false"})

    await _engine().scrape("https://www.linkedin.com/in/john-doe/", opts)

    assert httpx_mock.get_requests()[0].url.params["private"] == "false"


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500])
async def test_non_200_raises(httpx_mock: HTTPXMock, status: int) -> None:
    httpx_mock.add_response(status_code=status, json={"success": False, "message": "error"})

    with pytest.raises(EngineError):
        await _engine().scrape("https://www.amazon.com/dp/B07XYZ1234")


async def test_timeout_honored(httpx_mock: HTTPXMock) -> None:
    import httpx

    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))

    with pytest.raises(EngineError):
        await _engine().scrape("https://www.amazon.com/dp/B07XYZ1234", ScrapeOptions(timeout_s=5))


def test_is_available_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAPINGDOG_API_KEY", raising=False)
    assert ScrapingdogDedicatedEngine().is_available() is False
    monkeypatch.setenv("SCRAPINGDOG_API_KEY", _KEY)
    assert ScrapingdogDedicatedEngine().is_available() is True


def test_registered() -> None:
    assert get_engine("scrapingdog_dedicated") is ScrapingdogDedicatedEngine


@pytest.mark.parametrize(
    "site_class",
    ["linkedin_profile", "linkedin_company", "linkedin_job", "amazon_product", "twitter"],
)
def test_first_step_of_ai_utils_classes(site_class: str) -> None:
    ladder = get_ladder(site_class)
    assert step_engines(ladder[0]) == ("scrapingdog_dedicated",)
    # The previous ladder stays behind it as the fallback.
    assert len(ladder) > 1
