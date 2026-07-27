"""Tests for EnrichLayerEngine.

All tests use ``httpx_mock`` from pytest-httpx — no real network calls.
Follows the offline-by-default golden rule.

Contract pinned here (verified live 2026-07-27):
  - Method: GET
  - Base URL: https://enrichlayer.com
  - Auth: Authorization: Bearer <api_key> header
  - URL → endpoint routing (see _route_for)
  - Response: JSON object → ScrapeResult.json (+ text/markdown post-converted)
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.enrichlayer import EnrichLayerEngine, _adapt, _route_for
from scrapefold.options import ScrapeOptions

_BASE = "https://enrichlayer.com"
_API_KEY = "test-el-key-123"

# Trimmed real /api/v2/profile payload shape (Proxycurl-compatible snake_case).
_PAYLOAD = {
    "public_identifier": "williamhgates",
    "first_name": "Bill",
    "last_name": "Gates",
    "full_name": "Bill Gates",
    "follower_count": 40486634,
    "headline": "Chair, Gates Foundation and Founder, Breakthrough Energy",
    "experiences": [{"company": "Gates Foundation", "title": "Co-chair"}],
}


def _engine(api_key: str = _API_KEY) -> EnrichLayerEngine:
    return EnrichLayerEngine(api_key=api_key)


def _mock(httpx_mock: HTTPXMock, *, payload: dict | None = None, status_code: int = 200) -> None:
    httpx_mock.add_response(
        method="GET",
        status_code=status_code,
        json=payload if payload is not None else _PAYLOAD,
    )


# ---------------------------------------------------------------------------
# 1. Basic 200 JSON → json slot + text/markdown populated; cost set
# ---------------------------------------------------------------------------


async def test_basic_200_populates_json_text_markdown(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    result = await _engine().scrape("https://www.linkedin.com/in/williamhgates/")

    assert result.json == _PAYLOAD
    assert result.html is None
    assert "williamhgates" in result.text
    assert "williamhgates" in result.markdown
    assert result.markdown.startswith("```json")
    assert result.cost_usd == 0.02
    assert result.engine == "enrichlayer"


# ---------------------------------------------------------------------------
# 2. Authorization: Bearer header set on the outgoing request
# ---------------------------------------------------------------------------


async def test_bearer_auth_header_set(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    await _engine().scrape("https://www.linkedin.com/in/williamhgates/")

    request = httpx_mock.get_requests()[0]
    assert request.headers.get("Authorization") == f"Bearer {_API_KEY}"


# ---------------------------------------------------------------------------
# 3. URL → endpoint routing (the adapter matrix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,endpoint,param",
    [
        ("https://www.linkedin.com/in/john-doe/", "/api/v2/profile", "profile_url"),
        ("https://linkedin.com/in/jane", "/api/v2/profile", "profile_url"),
        ("https://www.linkedin.com/company/acme/", "/api/v2/company", "url"),
        ("https://www.linkedin.com/showcase/acme-cloud/", "/api/v2/company", "url"),
        ("https://www.linkedin.com/school/stanford-university/", "/api/v2/school", "url"),
        ("https://www.linkedin.com/jobs/view/1234567/", "/api/v2/job", "url"),
        ("https://x.com/gates", "/api/v2/profile", "twitter_profile_url"),
        ("https://twitter.com/gates", "/api/v2/profile", "twitter_profile_url"),
        ("https://www.facebook.com/zuck", "/api/v2/profile", "facebook_profile_url"),
    ],
)
def test_route_for_maps_urls(url: str, endpoint: str, param: str) -> None:
    got_endpoint, params = _route_for(url)
    assert got_endpoint == endpoint
    assert params == {param: url}


async def test_routing_hits_correct_endpoint(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    await _engine().scrape("https://www.linkedin.com/company/acme/")

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{_BASE}/api/v2/company")
    assert request.url.params.get("url") == "https://www.linkedin.com/company/acme/"


# ---------------------------------------------------------------------------
# 4. Unroutable URL → EngineError (wrapped ValueError)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/blog/post",
        "https://www.linkedin.com/posts/foo_activity-1",  # no post endpoint
        "https://x.com/gates/status/123",  # tweets are not people
    ],
)
def test_route_for_unknown_url_raises(url: str) -> None:
    with pytest.raises(ValueError, match="no endpoint"):
        _route_for(url)


async def test_unknown_url_raises_engine_error() -> None:
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://example.com/blog/post")
    assert exc_info.value.engine == "enrichlayer"


# ---------------------------------------------------------------------------
# 5. extra["enrichlayer_endpoint"] forces the endpoint path
# ---------------------------------------------------------------------------


async def test_extra_endpoint_override(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"enrichlayer_endpoint": "/api/v2/company"})
    await _engine().scrape("https://example.com/anything", opts)

    request = httpx_mock.get_requests()[0]
    assert str(request.url).startswith(f"{_BASE}/api/v2/company")
    # url auto-added because no target param supplied via extras
    assert request.url.params.get("url") == "https://example.com/anything"


def test_adapt_endpoint_override_with_target_param_skips_url() -> None:
    opts = ScrapeOptions(
        extra={
            "enrichlayer_endpoint": "/api/v2/profile/resolve",
            "enrichlayer_company_domain": "gatesfoundation.org",
            "enrichlayer_first_name": "Bill",
        }
    )
    endpoint, params = _adapt(opts, "https://whatever")
    assert endpoint == "/api/v2/profile/resolve"
    assert params == {"company_domain": "gatesfoundation.org", "first_name": "Bill"}
    assert "url" not in params


# ---------------------------------------------------------------------------
# 6. extra["enrichlayer_*"] forwarded as query params (prefix stripped)
# ---------------------------------------------------------------------------


async def test_extra_params_forwarded(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(extra={"enrichlayer_use_cache": "if-present"})
    await _engine().scrape("https://www.linkedin.com/in/williamhgates/", opts)

    request = httpx_mock.get_requests()[0]
    assert request.url.params.get("use_cache") == "if-present"


def test_adapt_ignores_non_enrichlayer_extra_keys() -> None:
    opts = ScrapeOptions(extra={"firecrawl_foo": "bar", "unrelated": "val"})
    _endpoint, params = _adapt(opts, "https://www.linkedin.com/in/jane")
    assert "foo" not in params
    assert "unrelated" not in params


# ---------------------------------------------------------------------------
# 7. Per-endpoint credit pricing (job = 2 credits)
# ---------------------------------------------------------------------------


async def test_job_endpoint_costs_two_credits(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock, payload={"title": "Engineer", "company": {"name": "Acme"}})
    result = await _engine().scrape("https://www.linkedin.com/jobs/view/1234567/")
    assert result.cost_usd == 0.04


# ---------------------------------------------------------------------------
# 8. Social normalization — LinkedIn person payload → Profile entity
# ---------------------------------------------------------------------------


async def test_social_normalized_for_person_profile(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    result = await _engine().scrape("https://www.linkedin.com/in/williamhgates/")

    assert result.social is not None
    assert result.social.platform == "linkedin"
    assert result.social.handle == "williamhgates"
    assert result.social.followers == 40486634
    assert result.social.raw is result.json


async def test_social_none_for_job(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock, payload={"title": "Engineer", "company": {"name": "Acme"}})
    result = await _engine().scrape("https://www.linkedin.com/jobs/view/1234567/")
    assert result.social is None


# ---------------------------------------------------------------------------
# 9. meta carries status_code + endpoint
# ---------------------------------------------------------------------------


async def test_meta_has_status_and_endpoint(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    result = await _engine().scrape("https://www.linkedin.com/in/williamhgates/")

    assert result.meta["status_code"] == 200
    assert result.meta["enrichlayer_endpoint"] == "/api/v2/profile"


# ---------------------------------------------------------------------------
# 10. is_available() True with key, False without
# ---------------------------------------------------------------------------


def test_is_available_true_with_key() -> None:
    assert _engine(api_key="key-abc").is_available() is True


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENRICHLAYER_API_KEY", raising=False)
    assert EnrichLayerEngine(api_key=None).is_available() is False


def test_api_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENRICHLAYER_API_KEY", "env-key")
    assert EnrichLayerEngine().api_key == "env-key"


# ---------------------------------------------------------------------------
# 11. Errors → EngineError
# ---------------------------------------------------------------------------


async def test_http_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    """403 out-of-credits / 401 bad key must surface as EngineError."""
    _mock(httpx_mock, payload={}, status_code=403)
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://www.linkedin.com/in/jane")
    assert exc_info.value.engine == "enrichlayer"


async def test_network_error_raises_engine_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    with pytest.raises(EngineError) as exc_info:
        await _engine().scrape("https://www.linkedin.com/in/jane")
    assert exc_info.value.engine == "enrichlayer"


# ---------------------------------------------------------------------------
# 12. Unsupported options are dropped (don't crash)
# ---------------------------------------------------------------------------


async def test_unsupported_options_dropped(httpx_mock: HTTPXMock) -> None:
    _mock(httpx_mock)
    opts = ScrapeOptions(render_js=True, stealth=True, country="us", wait_ms=9000)
    result = await _engine().scrape("https://www.linkedin.com/in/jane", opts)
    assert result.engine == "enrichlayer"


# ---------------------------------------------------------------------------
# 13. Registry + ladder wiring
# ---------------------------------------------------------------------------


def test_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "enrichlayer" in list_engine_names()
    assert get_engine("enrichlayer") is EnrichLayerEngine


def test_linkedin_ladders_include_enrichlayer() -> None:
    from scrapefold.ladders import get_ladder, step_engines

    for cls in ("linkedin_profile", "linkedin_company", "linkedin_job"):
        first = get_ladder(cls)[0]
        assert "enrichlayer" in step_engines(first), f"{cls} race missing enrichlayer"
