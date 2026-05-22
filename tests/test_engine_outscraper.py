"""Tests for the OutscraperEngine.

All tests run offline — no real network calls, no real API key required.
The SDK's ApiClient is monkeypatched with a MagicMock.

SDK contract pinned by introspection (2026-05-22):
  - company_insights(query, fields=None, async_request=False, enrichment=None) -> list|dict
  - No linkedin_profiles method exists; ALL URLs (company + profile) route to company_insights.
  - Return shape: list of dicts — each dict is one URL's structured data.
    Internally: _handle_response returns response.json().get('data', []) for sync calls,
    so the SDK returns list[dict] for a single-URL call.

Routing decision:
  - URL contains '/in/' → company_insights (profile URLs; no dedicated profile method)
  - All other LinkedIn URLs → company_insights
  - We ALWAYS use company_insights regardless of URL pattern; documented in engine docstring.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.engines.outscraper import OutscraperEngine
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_COMPANY_URL = "https://www.linkedin.com/company/acme"
_PROFILE_URL = "https://www.linkedin.com/in/john-doe"
_GENERIC_URL = "https://example.com"

_SAMPLE_COMPANY_DATA = {
    "name": "Acme Corp",
    "founded": 2005,
    "size": "51-200 employees",
    "industry": "Technology",
}

_SAMPLE_PROFILE_DATA = {
    "name": "John Doe",
    "headline": "Software Engineer",
    "location": "San Francisco",
}


def _make_client_cls(return_value=None) -> MagicMock:
    """Return a MagicMock mimicking ApiClient class.

    The mock's ``return_value`` (the instance) has a ``company_insights``
    synchronous method that returns the given list.
    """
    if return_value is None:
        return_value = [_SAMPLE_COMPANY_DATA]
    instance = MagicMock()
    instance.company_insights = MagicMock(return_value=return_value)
    cls = MagicMock(return_value=instance)
    return cls


# ---------------------------------------------------------------------------
# 1. Basic call success
# ---------------------------------------------------------------------------


async def test_basic_call_success() -> None:
    """Successful SDK call → ScrapeResult with json set, text non-empty, cost=0.003."""
    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        result = await engine.scrape(_COMPANY_URL)

    assert isinstance(result, ScrapeResult)
    assert result.json == _SAMPLE_COMPANY_DATA
    assert result.text  # must be non-empty
    assert result.markdown  # must be non-empty
    assert result.cost_usd == 0.003
    assert result.engine == "outscraper"
    assert result.html is None


# ---------------------------------------------------------------------------
# 2. Company URL routes to company_insights
# ---------------------------------------------------------------------------


async def test_company_url_routes_to_company_insights() -> None:
    """URL like linkedin.com/company/acme triggers company_insights."""
    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        await engine.scrape(_COMPANY_URL)

    instance = client_cls.return_value
    instance.company_insights.assert_called_once()
    # First positional arg must be a list containing the URL
    call_args = instance.company_insights.call_args
    query_arg = call_args[0][0] if call_args[0] else call_args[1].get("query")
    assert _COMPANY_URL in query_arg


# ---------------------------------------------------------------------------
# 3. Profile URL also routes to company_insights (no dedicated profile method)
# ---------------------------------------------------------------------------


async def test_profile_url_routes_to_company_insights() -> None:
    """URL like linkedin.com/in/john also uses company_insights.

    The outscraper SDK has no dedicated linkedin_profiles method.
    All LinkedIn URLs — company and profile — go through company_insights.
    This is documented in the engine docstring.
    """
    client_cls = _make_client_cls([_SAMPLE_PROFILE_DATA])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        result = await engine.scrape(_PROFILE_URL)

    instance = client_cls.return_value
    instance.company_insights.assert_called_once()
    assert result.json == _SAMPLE_PROFILE_DATA


# ---------------------------------------------------------------------------
# 4. First item used when SDK returns a list
# ---------------------------------------------------------------------------


async def test_first_item_used_when_sdk_returns_list() -> None:
    """When SDK returns multiple items, only the first is used."""
    second_item = {"name": "Other"}
    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA, second_item])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        result = await engine.scrape(_COMPANY_URL)

    assert result.json == _SAMPLE_COMPANY_DATA


# ---------------------------------------------------------------------------
# 5. Empty response raises EngineError
# ---------------------------------------------------------------------------


async def test_empty_response_raises_engine_error() -> None:
    """SDK returns [] → engine raises EngineError (wrapped by base class)."""
    client_cls = _make_client_cls([])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_COMPANY_URL)

    assert exc_info.value.engine == "outscraper"


# ---------------------------------------------------------------------------
# 6. Extra outscraper_* keys forwarded as SDK kwargs
# ---------------------------------------------------------------------------


async def test_extra_passthrough_to_sdk_kwargs() -> None:
    """opts.extra["outscraper_fields"]="name" becomes SDK kwarg fields="name"."""
    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        opts = ScrapeOptions(extra={"outscraper_fields": "name,size"})
        await engine.scrape(_COMPANY_URL, opts)

    instance = client_cls.return_value
    call_kwargs = instance.company_insights.call_args[1]
    assert call_kwargs.get("fields") == "name,size"


# ---------------------------------------------------------------------------
# 7. is_available true with key
# ---------------------------------------------------------------------------


def test_is_available_true_with_key() -> None:
    engine = OutscraperEngine(api_key="os-key")
    assert engine.is_available() is True


# ---------------------------------------------------------------------------
# 8. is_available false without key
# ---------------------------------------------------------------------------


def test_is_available_false_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OUTSCRAPER_API_KEY", raising=False)
    engine = OutscraperEngine(api_key=None)
    assert engine.is_available() is False


# ---------------------------------------------------------------------------
# 9. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    """An SDK RuntimeError must surface as an EngineError from the base class."""
    instance = MagicMock()
    instance.company_insights = MagicMock(side_effect=RuntimeError("quota exceeded"))
    client_cls = MagicMock(return_value=instance)

    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_COMPANY_URL)

    assert exc_info.value.engine == "outscraper"
    assert "quota exceeded" in exc_info.value.message


# ---------------------------------------------------------------------------
# 10. Sync SDK call runs in thread (does not block event loop)
# ---------------------------------------------------------------------------


async def test_sync_sdk_runs_in_thread() -> None:
    """Engine must use asyncio.to_thread to wrap the sync SDK call."""
    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA])

    to_thread_calls: list = []
    original_to_thread = asyncio.to_thread

    async def spy_to_thread(fn, *args, **kwargs):  # type: ignore[override]
        to_thread_calls.append(fn)
        return await original_to_thread(fn, *args, **kwargs)

    with (
        patch("scrapefold.engines.outscraper.ApiClient", client_cls),
        patch("asyncio.to_thread", spy_to_thread),
    ):
        engine = OutscraperEngine(api_key="os-test")
        await engine.scrape(_COMPANY_URL)

    assert len(to_thread_calls) == 1, (
        f"Expected asyncio.to_thread to be called once, got {len(to_thread_calls)} calls. "
        "The sync SDK call must be wrapped in to_thread to avoid blocking the event loop."
    )


# ---------------------------------------------------------------------------
# 11. Unsupported options silently dropped
# ---------------------------------------------------------------------------


async def test_unsupported_options_silently_dropped() -> None:
    """Options not in SUPPORTED_OPTIONS are stripped without raising."""
    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        opts = ScrapeOptions(
            render_js=False,
            stealth=True,
            country="ru",
            wait_ms=9000,
            take_screenshot=True,
        )
        # Must not raise
        result = await engine.scrape(_COMPANY_URL, opts)

    assert result.engine == "outscraper"


# ---------------------------------------------------------------------------
# 12. Regression: kwargs match SDK signature (no params= wrapper)
# ---------------------------------------------------------------------------


async def test_regression_kwargs_match_sdk_signature() -> None:
    """Guard against the Pack 2A class of bug: passing options wrapped in params={}.

    company_insights signature: (query, fields=None, async_request=False, enrichment=None)
    The engine must call it with real kwargs, not wrapped under a 'params' key.

    Skipped when the outscraper SDK is not installed (default ``[test]`` extra
    excludes it); guard fires only when contributors opt into ``[outscraper]``.
    """
    real_module = pytest.importorskip("outscraper")
    real_api_client = real_module.ApiClient
    valid_param_names = set(inspect.signature(real_api_client.company_insights).parameters.keys())
    valid_param_names.discard("self")  # not a kwarg

    client_cls = _make_client_cls([_SAMPLE_COMPANY_DATA])
    with patch("scrapefold.engines.outscraper.ApiClient", client_cls):
        engine = OutscraperEngine(api_key="os-test")
        # Pass an extra key to trigger extra passthrough
        opts = ScrapeOptions(extra={"outscraper_fields": "name"})
        await engine.scrape(_COMPANY_URL, opts)

    instance = client_cls.return_value
    call_kwargs = instance.company_insights.call_args[1]

    for key in call_kwargs:
        assert key in valid_param_names, (
            f"Engine passed unknown kwarg {key!r} to company_insights. "
            f"Valid params: {sorted(valid_param_names)}. "
            "This is the Pack 2A bug pattern — wrapping options instead of using real kwargs."
        )

    # Specifically, 'params' must never be in the kwargs
    assert "params" not in call_kwargs, (
        "Engine wrapped options under params= — the SDK ignores that silently."
    )
