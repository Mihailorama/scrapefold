"""Tests for the ApifyLinkedInEngine.

All tests run offline — no real network calls, no real API key required.
The SDK's ApifyClientAsync is monkeypatched with AsyncMock/MagicMock.

Regression guard: the engine MUST call ``actor(...).call(run_input=...)``
using ``run_input`` as the keyword name. NOT ``input``, NOT ``params``.
(Mirrors the Pack 2A lesson from firecrawl: wrong kwarg = silent failure.)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_TEST_URL = "https://www.linkedin.com/in/satya-nadella/"

_DEFAULT_ACTOR = "apimaestro/linkedin-profile-detail"

_SAMPLE_PROFILE = {
    "profileUrl": _TEST_URL,
    "firstName": "Satya",
    "lastName": "Nadella",
    "headline": "CEO at Microsoft",
    "summary": "Empowering every person and every organization on the planet.",
}


def _make_run_mock(dataset_id: str = "ds-abc123", run_id: str = "run-xyz789") -> MagicMock:
    """Return a mock mimicking the Run pydantic model returned by actor().call()."""
    run = MagicMock()
    run.id = run_id
    run.default_dataset_id = dataset_id
    return run


def _make_dataset_page(items: list[dict]) -> MagicMock:
    """Return a mock mimicking DatasetItemsPage."""
    page = MagicMock()
    page.items = items
    return page


def _make_client_async_cls(
    run: MagicMock | None = None,
    dataset_items: list[dict] | None = None,
    dataset_id: str = "ds-abc123",
    run_id: str = "run-xyz789",
) -> MagicMock:
    """Build a fully mocked ApifyClientAsync class.

    The returned object is the *class* (callable); calling it returns an
    instance whose methods mirror the real SDK.
    """
    if run is None:
        run = _make_run_mock(dataset_id=dataset_id, run_id=run_id)
    if dataset_items is None:
        dataset_items = [_SAMPLE_PROFILE]

    # actor_client.call() is async
    actor_client = MagicMock()
    actor_client.call = AsyncMock(return_value=run)

    # dataset_client.list_items() is async
    dataset_client = MagicMock()
    dataset_client.list_items = AsyncMock(return_value=_make_dataset_page(dataset_items))

    # client instance
    client_instance = MagicMock()
    client_instance.actor = MagicMock(return_value=actor_client)
    client_instance.dataset = MagicMock(return_value=dataset_client)

    # class (callable -> client instance)
    client_cls = MagicMock(return_value=client_instance)
    return client_cls


# ---------------------------------------------------------------------------
# 1. Basic success
# ---------------------------------------------------------------------------


async def test_basic_call_success() -> None:
    """Successful actor call produces ScrapeResult with json populated."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        result = await engine.scrape(_TEST_URL)

    assert isinstance(result, ScrapeResult)
    assert result.json == _SAMPLE_PROFILE
    assert result.text  # non-empty (pretty-printed JSON)
    assert result.markdown  # non-empty
    assert result.cost_usd == 0.0015
    assert result.engine == "apify_linkedin"
    # LinkedIn profile payload is normalized into ScrapeResult.social.
    from scrapefold.social import Profile

    assert isinstance(result.social, Profile)
    assert result.social.platform == "linkedin"
    assert result.social.name == "Satya Nadella"  # composed from firstName + lastName
    assert result.social.bio == _SAMPLE_PROFILE["summary"]


# ---------------------------------------------------------------------------
# 2. Actor ID from extra overrides default
# ---------------------------------------------------------------------------


async def test_actor_id_from_extra_overrides_default() -> None:
    """opts.extra['apify_actor_id'] is used instead of the module default."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        opts = ScrapeOptions(extra={"apify_actor_id": "customer/custom-actor"})
        await engine.scrape(_TEST_URL, opts)

    client_instance = client_cls.return_value
    client_instance.actor.assert_called_once_with("customer/custom-actor")


# ---------------------------------------------------------------------------
# 3. Default actor ID used when extra missing
# ---------------------------------------------------------------------------


async def test_default_actor_id_used_when_extra_missing() -> None:
    """Falls back to DEFAULT_ACTOR_ID when extra has no apify_actor_id."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        await engine.scrape(_TEST_URL)

    client_instance = client_cls.return_value
    client_instance.actor.assert_called_once_with(_DEFAULT_ACTOR)


# ---------------------------------------------------------------------------
# 4. Profile URL passed to actor run_input
# ---------------------------------------------------------------------------


async def test_profile_url_passed_to_actor_input() -> None:
    """The target URL appears in the run_input dict passed to actor().call()."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        await engine.scrape(_TEST_URL)

    actor_client = client_cls.return_value.actor.return_value
    call_kwargs = actor_client.call.call_args.kwargs
    run_input = call_kwargs["run_input"]
    # The URL should appear somewhere in run_input (either under profileUrls or startUrls)
    input_str = str(run_input)
    assert _TEST_URL in input_str, f"URL not found in run_input: {run_input!r}"


# ---------------------------------------------------------------------------
# 5. First dataset item used when multiple returned
# ---------------------------------------------------------------------------


async def test_first_dataset_item_used() -> None:
    """When list_items() returns multiple items, only the first is used."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    items = [
        {"name": "First Person"},
        {"name": "Second Person"},
        {"name": "Third Person"},
    ]
    client_cls = _make_client_async_cls(dataset_items=items)
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        result = await engine.scrape(_TEST_URL)

    assert result.json == {"name": "First Person"}


# ---------------------------------------------------------------------------
# 6. Empty dataset raises EngineError
# ---------------------------------------------------------------------------


async def test_empty_dataset_raises_engine_error() -> None:
    """No items returned from dataset → EngineError is raised."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls(dataset_items=[])
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "apify_linkedin"


# ---------------------------------------------------------------------------
# 7. apify_* extra keys forwarded as run_input keys (un-prefixed)
# ---------------------------------------------------------------------------


async def test_apify_extra_passthrough() -> None:
    """opts.extra['apify_proxyConfiguration'] is forwarded as 'proxyConfiguration' in run_input."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    proxy_cfg = {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}
    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        opts = ScrapeOptions(extra={"apify_proxyConfiguration": proxy_cfg})
        await engine.scrape(_TEST_URL, opts)

    actor_client = client_cls.return_value.actor.return_value
    run_input = actor_client.call.call_args.kwargs["run_input"]
    assert run_input.get("proxyConfiguration") == proxy_cfg


# ---------------------------------------------------------------------------
# 8. is_available() with / without token
# ---------------------------------------------------------------------------


def test_is_available_true_with_token() -> None:
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    assert ApifyLinkedInEngine(api_key="apify-token").is_available() is True


def test_is_available_false_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    assert ApifyLinkedInEngine(api_key=None).is_available() is False


# ---------------------------------------------------------------------------
# 9. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    """RuntimeError from the SDK becomes EngineError with engine='apify_linkedin'."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    actor_client = MagicMock()
    actor_client.call = AsyncMock(side_effect=RuntimeError("actor quota exceeded"))
    client_instance = MagicMock()
    client_instance.actor = MagicMock(return_value=actor_client)
    client_cls = MagicMock(return_value=client_instance)

    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_TEST_URL)

    assert exc_info.value.engine == "apify_linkedin"
    assert "actor quota exceeded" in exc_info.value.message


# ---------------------------------------------------------------------------
# 10. meta contains run_id and dataset_id
# ---------------------------------------------------------------------------


async def test_meta_contains_run_id_and_dataset_id() -> None:
    """ScrapeResult.meta must include actor_run_id and dataset_id."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls(dataset_id="ds-999", run_id="run-777")
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        result = await engine.scrape(_TEST_URL)

    assert result.meta.get("actor_run_id") == "run-777"
    assert result.meta.get("dataset_id") == "ds-999"


# ---------------------------------------------------------------------------
# 11. Unsupported options silently dropped (no raise)
# ---------------------------------------------------------------------------


async def test_unsupported_options_silently_dropped() -> None:
    """Passing stealth=True and render_js=False must not raise; they are stripped."""
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        # Neither stealth nor render_js are in SUPPORTED_OPTIONS — must not raise
        result = await engine.scrape(_TEST_URL, ScrapeOptions(stealth=True, render_js=False))

    assert isinstance(result, ScrapeResult)


# ---------------------------------------------------------------------------
# 12. Regression: kwargs match SDK signature (run_input, not input/params)
# ---------------------------------------------------------------------------


async def test_regression_kwargs_match_sdk_signature() -> None:
    """actor().call() must be called with run_input= keyword, never input= or params=.

    This is the Pack 2A regression guard: wrong kwarg name → SDK silently uses
    defaults, billing the caller while ignoring the target URL entirely.
    """
    from scrapefold.engines.apify_linkedin import ApifyLinkedInEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_linkedin.ApifyClientAsync", client_cls):
        engine = ApifyLinkedInEngine(api_key="apify-test")
        await engine.scrape(_TEST_URL)

    actor_client = client_cls.return_value.actor.return_value
    call_kwargs = actor_client.call.call_args.kwargs

    assert "run_input" in call_kwargs, (
        f"Expected run_input= kwarg but got: {list(call_kwargs.keys())}. "
        "The apify-client SDK ignores unknown kwargs, causing silent data-loss."
    )
    assert "input" not in call_kwargs, "Passed 'input' instead of 'run_input'"
    assert "params" not in call_kwargs, "Passed 'params' instead of 'run_input'"
