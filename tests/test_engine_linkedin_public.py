"""Offline tests for the keyless LinkedIn public-page engine (SCRAPEFOLD-32)."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines import get_engine
from scrapefold.engines.base import EngineError
from scrapefold.engines.linkedin_public import LinkedInPublicEngine, parse_public_page
from scrapefold.ladders import flatten_ladder, get_ladder

_URL = "https://www.linkedin.com/in/jane-doe"
_PERSON = {
    "@context": "http://schema.org",
    "@type": "Person",
    "name": "Jane Doe",
    "jobTitle": ["CTO"],
    "worksFor": [{"@type": "Organization", "name": "Acme"}],
    "address": {"@type": "PostalAddress", "addressLocality": "Berlin"},
}
_POST = {"@type": "Article", "headline": "Hello", "url": "https://www.linkedin.com/pulse/x"}


def _page(*objs: dict) -> str:
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in objs)
    return (
        '<html><head><meta property="og:title" content="Jane Doe - Acme | LinkedIn">'
        f"{scripts}</head><body></body></html>"
    )


def test_parse_picks_main_entity_and_posts() -> None:
    payload = parse_public_page(_page(_POST, {"@graph": [_PERSON]}))
    assert payload is not None
    assert payload["type"] == "Person"
    assert payload["entity"]["name"] == "Jane Doe"
    assert payload["posts"] == [_POST]
    assert payload["title"] == "Jane Doe - Acme | LinkedIn"


def test_parse_auth_wall_returns_none() -> None:
    assert parse_public_page("<html><title>Sign Up | LinkedIn</title></html>") is None


async def test_falls_back_to_browser_user_agent(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_URL, status_code=999, text="")
    httpx_mock.add_response(url=_URL, text=_page(_PERSON))

    result = await LinkedInPublicEngine().scrape(_URL)

    agents = [r.headers["user-agent"] for r in httpx_mock.get_requests()]
    assert "Googlebot" in agents[0] and "Chrome" in agents[1]
    assert result.json["entity"]["name"] == "Jane Doe"
    assert "Jane Doe" in result.text
    assert result.cost_usd == 0.0


async def test_no_entity_raises_so_ladder_escalates(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_URL, text="<html>authwall</html>", is_reusable=True)
    with pytest.raises(EngineError):
        await LinkedInPublicEngine().scrape(_URL)


def test_registered_and_first_in_linkedin_ladders() -> None:
    assert get_engine("linkedin_public") is LinkedInPublicEngine
    for cls in ("linkedin_profile", "linkedin_company", "linkedin_job"):
        assert flatten_ladder(get_ladder(cls))[:2] == ("linkedin_public", "keenable")
