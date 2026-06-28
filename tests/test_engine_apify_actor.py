"""Tests for the universal ApifyActorEngine.

All tests run offline — no real network calls, no real API key required.
The SDK's ApifyClientAsync is monkeypatched with AsyncMock/MagicMock.

Regression guard (shared with apify_linkedin): the engine MUST call
``actor(...).call(run_input=...)`` using ``run_input`` as the keyword name.
NOT ``input``, NOT ``params`` — the SDK silently ignores unknown kwargs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

_IG_URL = "https://www.instagram.com/natgeo/"

_SAMPLE_POST = {
    "url": _IG_URL,
    "ownerUsername": "natgeo",
    "caption": "A photo of the planet.",
    "likesCount": 12345,
}


def _make_run_mock(dataset_id: str = "ds-abc123", run_id: str = "run-xyz789") -> MagicMock:
    run = MagicMock()
    run.id = run_id
    run.default_dataset_id = dataset_id
    return run


def _make_dataset_page(items: list[dict]) -> MagicMock:
    page = MagicMock()
    page.items = items
    return page


def _make_client_async_cls(
    dataset_items: list[dict] | None = None,
    dataset_id: str = "ds-abc123",
    run_id: str = "run-xyz789",
) -> MagicMock:
    """Build a fully mocked ApifyClientAsync class (callable -> instance)."""
    if dataset_items is None:
        dataset_items = [_SAMPLE_POST]

    run = _make_run_mock(dataset_id=dataset_id, run_id=run_id)

    actor_client = MagicMock()
    actor_client.call = AsyncMock(return_value=run)

    dataset_client = MagicMock()
    dataset_client.list_items = AsyncMock(return_value=_make_dataset_page(dataset_items))

    client_instance = MagicMock()
    client_instance.actor = MagicMock(return_value=actor_client)
    client_instance.dataset = MagicMock(return_value=dataset_client)

    return MagicMock(return_value=client_instance)


# ---------------------------------------------------------------------------
# 1. Basic success
# ---------------------------------------------------------------------------


async def test_basic_call_success() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        result = await engine.scrape(_IG_URL)

    assert isinstance(result, ScrapeResult)
    assert result.json == _SAMPLE_POST
    assert result.text
    assert result.markdown
    assert result.cost_usd == 0.0015
    assert result.engine == "apify_actor"


# ---------------------------------------------------------------------------
# 2. Default actor routing per platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected_actor",
    [
        ("https://www.instagram.com/natgeo/", "apify/instagram-scraper"),
        ("https://www.tiktok.com/@nasa", "clockworks/tiktok-scraper"),
        ("https://x.com/nasa/status/1", "apidojo/tweet-scraper"),
        ("https://twitter.com/nasa", "apidojo/tweet-scraper"),
        ("https://www.youtube.com/watch?v=abc", "streamers/youtube-scraper"),
        ("https://youtu.be/abc", "streamers/youtube-scraper"),
        ("https://www.facebook.com/natgeo", "apify/facebook-posts-scraper"),
        ("https://www.reddit.com/r/space/", "trudax/reddit-scraper"),
        ("https://www.linkedin.com/in/foo/", "apimaestro/linkedin-profile-detail"),
    ],
)
async def test_default_actor_routing(url: str, expected_actor: str) -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        await engine.scrape(url)

    client_cls.return_value.actor.assert_called_once_with(expected_actor)


# ---------------------------------------------------------------------------
# 3. Explicit actor id from extra overrides the default
# ---------------------------------------------------------------------------


async def test_actor_id_from_extra_overrides_default() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        opts = ScrapeOptions(extra={"apify_actor_id": "customer/custom-actor"})
        await engine.scrape(_IG_URL, opts)

    client_cls.return_value.actor.assert_called_once_with("customer/custom-actor")


# ---------------------------------------------------------------------------
# 4. Unknown host with no override raises EngineError
# ---------------------------------------------------------------------------


async def test_unknown_host_without_override_raises() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape("https://example.com/page")

    assert exc_info.value.engine == "apify_actor"


async def test_unknown_host_with_override_succeeds() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        opts = ScrapeOptions(extra={"apify_actor_id": "some/actor"})
        result = await engine.scrape("https://example.com/page", opts)

    assert isinstance(result, ScrapeResult)
    client_cls.return_value.actor.assert_called_once_with("some/actor")


# ---------------------------------------------------------------------------
# 5. URL passed into run_input
# ---------------------------------------------------------------------------


async def test_url_passed_to_actor_input() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        await engine.scrape(_IG_URL)

    actor_client = client_cls.return_value.actor.return_value
    run_input = actor_client.call.call_args.kwargs["run_input"]
    assert _IG_URL in str(run_input)
    assert run_input["startUrls"] == [{"url": _IG_URL}]


# ---------------------------------------------------------------------------
# 6. Multi-item dataset returns the full list
# ---------------------------------------------------------------------------


async def test_multi_item_dataset_returns_list() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    items = [{"id": 1}, {"id": 2}, {"id": 3}]
    client_cls = _make_client_async_cls(dataset_items=items)
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        result = await engine.scrape(_IG_URL)

    assert result.json == items
    assert result.meta.get("item_count") == 3


async def test_single_item_dataset_returns_object() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls(dataset_items=[{"id": 1}])
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        result = await engine.scrape(_IG_URL)

    assert result.json == {"id": 1}
    assert result.meta.get("item_count") == 1


# ---------------------------------------------------------------------------
# 7. Empty dataset raises EngineError
# ---------------------------------------------------------------------------


async def test_empty_dataset_raises_engine_error() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls(dataset_items=[])
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_IG_URL)

    assert exc_info.value.engine == "apify_actor"


# ---------------------------------------------------------------------------
# 8. apify_* extras forwarded into run_input (prefix stripped)
# ---------------------------------------------------------------------------


async def test_apify_extra_passthrough() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        opts = ScrapeOptions(extra={"apify_resultsLimit": 50, "apify_actor_id": "x/y"})
        await engine.scrape(_IG_URL, opts)

    actor_client = client_cls.return_value.actor.return_value
    run_input = actor_client.call.call_args.kwargs["run_input"]
    assert run_input.get("resultsLimit") == 50
    # actor_id is routing metadata, not actor input
    assert "actor_id" not in run_input


# ---------------------------------------------------------------------------
# 9. is_available() with / without token
# ---------------------------------------------------------------------------


def test_is_available_true_with_token() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    assert ApifyActorEngine(api_key="apify-token").is_available() is True


def test_is_available_false_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    from scrapefold.engines.apify_actor import ApifyActorEngine

    assert ApifyActorEngine(api_key=None).is_available() is False


# ---------------------------------------------------------------------------
# 10. SDK exception wrapped in EngineError
# ---------------------------------------------------------------------------


async def test_sdk_exception_wrapped_in_engine_error() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    actor_client = MagicMock()
    actor_client.call = AsyncMock(side_effect=RuntimeError("actor quota exceeded"))
    client_instance = MagicMock()
    client_instance.actor = MagicMock(return_value=actor_client)
    client_cls = MagicMock(return_value=client_instance)

    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        with pytest.raises(EngineError) as exc_info:
            await engine.scrape(_IG_URL)

    assert exc_info.value.engine == "apify_actor"
    assert "actor quota exceeded" in exc_info.value.message


# ---------------------------------------------------------------------------
# 11. meta contains run_id, dataset_id, actor_id
# ---------------------------------------------------------------------------


async def test_meta_contains_run_and_actor_ids() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls(dataset_id="ds-999", run_id="run-777")
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        result = await engine.scrape(_IG_URL)

    assert result.meta.get("actor_run_id") == "run-777"
    assert result.meta.get("dataset_id") == "ds-999"
    assert result.meta.get("actor_id") == "apify/instagram-scraper"


# ---------------------------------------------------------------------------
# 12. Unsupported options silently dropped (no raise)
# ---------------------------------------------------------------------------


async def test_unsupported_options_silently_dropped() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        result = await engine.scrape(_IG_URL, ScrapeOptions(stealth=True, render_js=False))

    assert isinstance(result, ScrapeResult)


# ---------------------------------------------------------------------------
# 13. Regression: kwargs match SDK signature (run_input, not input/params)
# ---------------------------------------------------------------------------


async def test_regression_kwargs_match_sdk_signature() -> None:
    from scrapefold.engines.apify_actor import ApifyActorEngine

    client_cls = _make_client_async_cls()
    with patch("scrapefold.engines.apify_actor.ApifyClientAsync", client_cls):
        engine = ApifyActorEngine(api_key="apify-test")
        await engine.scrape(_IG_URL)

    call_kwargs = client_cls.return_value.actor.return_value.call.call_args.kwargs
    assert "run_input" in call_kwargs
    assert "input" not in call_kwargs
    assert "params" not in call_kwargs


# ---------------------------------------------------------------------------
# 14. Engine resolves via the public registry + "apify" alias
# ---------------------------------------------------------------------------


def test_registry_resolves_apify_actor_and_alias() -> None:
    from scrapefold.engines import get_engine, resolve_alias
    from scrapefold.engines.apify_actor import ApifyActorEngine

    assert resolve_alias("apify") == "apify_actor"
    assert get_engine("apify_actor") is ApifyActorEngine
    assert get_engine("apify") is ApifyActorEngine
