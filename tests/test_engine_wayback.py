"""Tests for the Wayback Machine fallback engine."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from scrapefold.engines.base import EngineError
from scrapefold.engines.wayback import WaybackEngine, _raw_snapshot_url
from scrapefold.options import ScrapeOptions

_URL = "https://example.com/dead-page"
_SNAPSHOT_URL = "http://web.archive.org/web/20260101000000/https://example.com/dead-page"
_AVAILABILITY = "https://archive.org/wayback/available"


def _availability_payload(available: bool = True) -> dict:
    if not available:
        return {"archived_snapshots": {}}
    return {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "url": _SNAPSHOT_URL,
                "timestamp": "20260101000000",
                "status": "200",
            }
        }
    }


def test_raw_snapshot_url_inserts_id_suffix() -> None:
    assert (
        _raw_snapshot_url(_SNAPSHOT_URL)
        == "http://web.archive.org/web/20260101000000id_/https://example.com/dead-page"
    )


def test_raw_snapshot_url_leaves_unrecognized_urls_alone() -> None:
    assert _raw_snapshot_url("https://example.com/x") == "https://example.com/x"


async def test_fetch_returns_archived_snapshot_marked_honestly(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_AVAILABILITY}?url=https%3A%2F%2Fexample.com%2Fdead-page",
        json=_availability_payload(),
    )
    httpx_mock.add_response(
        url="http://web.archive.org/web/20260101000000id_/https://example.com/dead-page",
        html="<html><body><h1>Recovered</h1><p>archived body</p></body></html>",
    )

    result = await WaybackEngine().scrape(_URL, ScrapeOptions())

    assert result.engine == "wayback"
    assert "Recovered" in result.markdown
    assert result.json["source"] == "archive.org"
    assert result.json["snapshot_timestamp"] == "20260101000000"
    assert result.json["snapshot_url"] == _SNAPSHOT_URL
    assert result.meta["archived"] is True
    assert result.cost_usd == 0.0


async def test_fetch_raises_engine_error_when_no_snapshot(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_AVAILABILITY}?url=https%3A%2F%2Fexample.com%2Fdead-page",
        json=_availability_payload(available=False),
    )

    with pytest.raises(EngineError, match="no archived snapshot"):
        await WaybackEngine().scrape(_URL, ScrapeOptions())


async def test_fetch_pins_timestamp_from_extra(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_AVAILABILITY}?url=https%3A%2F%2Fexample.com%2Fdead-page&timestamp=20240601",
        json=_availability_payload(),
    )
    httpx_mock.add_response(
        url="http://web.archive.org/web/20260101000000id_/https://example.com/dead-page",
        html="<html><body>pinned</body></html>",
    )

    opts = ScrapeOptions(extra={"wayback_timestamp": "20240601"})
    result = await WaybackEngine().scrape(_URL, opts)
    assert "pinned" in result.text


async def test_fetch_wraps_availability_http_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{_AVAILABILITY}?url=https%3A%2F%2Fexample.com%2Fdead-page",
        status_code=503,
    )

    with pytest.raises(EngineError):
        await WaybackEngine().scrape(_URL, ScrapeOptions())


def test_registered_in_lazy_registry() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "wayback" in list_engine_names()
    assert get_engine("wayback") is WaybackEngine


def test_is_available_without_api_key() -> None:
    assert WaybackEngine().is_available() is True


def test_capabilities_are_free_and_keyless() -> None:
    caps = WaybackEngine.CAPABILITIES
    assert caps.requires_api_key is False
    assert caps.free_tier is True
    assert caps.estimated_cost_usd == 0.0


def test_module_docstring_documents_param_table() -> None:
    import scrapefold.engines.wayback as mod

    assert "wayback_timestamp" in (mod.__doc__ or "")


def test_json_slot_shape_is_json_serializable(httpx_mock: HTTPXMock) -> None:
    payload = _availability_payload()
    assert json.dumps(payload)  # sanity: fixtures stay serializable
