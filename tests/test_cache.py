"""Tests for scrapefold.cache — disk-backed sha256-keyed TTL cache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scrapefold.cache import Cache, make_key
from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult


def _result(url: str, md: str = "# hello") -> ScrapeResult:
    return ScrapeResult(
        url=url,
        text=md,
        markdown=md,
        html=None,
        engine="stub",
        elapsed_ms=1,
    )


# ---------------------------------------------------------------------------
# 1. make_key — same (url, opts) → same key; differing → different key
# ---------------------------------------------------------------------------


def test_make_key_deterministic_for_same_inputs() -> None:
    k1 = make_key("https://example.com/", ScrapeOptions(language="en"))
    k2 = make_key("https://example.com/", ScrapeOptions(language="en"))
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_make_key_changes_on_url() -> None:
    k1 = make_key("https://a.com/", ScrapeOptions())
    k2 = make_key("https://b.com/", ScrapeOptions())
    assert k1 != k2


def test_make_key_changes_on_opts() -> None:
    k1 = make_key("https://example.com/", ScrapeOptions(language="en"))
    k2 = make_key("https://example.com/", ScrapeOptions(language="ru"))
    assert k1 != k2


def test_make_key_stable_across_dict_key_order() -> None:
    # extra dict key ordering should not influence the key
    k1 = make_key("https://x/", ScrapeOptions(extra={"a": 1, "b": 2}))
    k2 = make_key("https://x/", ScrapeOptions(extra={"b": 2, "a": 1}))
    assert k1 == k2


# ---------------------------------------------------------------------------
# 1a. Strict canonicalization — Policy dataclass in extra is fine
# ---------------------------------------------------------------------------


def test_make_key_handles_policy_dataclass_in_extra() -> None:
    from scrapefold import Policy

    k1 = make_key("https://x/", ScrapeOptions(extra={"policy": Policy(paid_allowed=False)}))
    k2 = make_key("https://x/", ScrapeOptions(extra={"policy": Policy(paid_allowed=False)}))
    k3 = make_key("https://x/", ScrapeOptions(extra={"policy": Policy(paid_allowed=True)}))

    assert k1 == k2
    assert k1 != k3


# ---------------------------------------------------------------------------
# 1b. Strict canonicalization — non-serializable extra value bypasses cache
# ---------------------------------------------------------------------------


def test_make_key_returns_none_on_unserializable_extra(caplog: pytest.LogCaptureFixture) -> None:
    """A callable / arbitrary object in extra → cache bypass (returns None)
    with a logged warning instead of producing a non-deterministic key."""

    async def my_callback() -> str:
        return "x"

    key = make_key("https://x/", ScrapeOptions(extra={"on_done": my_callback}))
    assert key is None
    assert any("not canonicalizable" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. Cache hit / miss
# ---------------------------------------------------------------------------


async def test_cache_miss_then_hit(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "abc123"

    assert await cache.get(key) is None
    await cache.set(key, _result("https://x/"))
    got = await cache.get(key)

    assert got is not None
    assert got.url == "https://x/"
    assert got.markdown == "# hello"


# ---------------------------------------------------------------------------
# 3. TTL expiry — old entries return None
# ---------------------------------------------------------------------------


async def test_cache_ttl_expiry(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "exp"

    await cache.set(key, _result("https://x/"))

    # Force the file's mtime into the past (8 days)
    path = cache._path_for(key)
    old = time.time() - 8 * 86400
    import os

    os.utime(path, (old, old))

    assert await cache.get(key) is None


# ---------------------------------------------------------------------------
# 4. Atomic write — partial writes do not poison the cache
# ---------------------------------------------------------------------------


async def test_cache_write_is_atomic(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "atom"
    await cache.set(key, _result("https://x/"))

    # No half-written .tmp files left behind
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


# ---------------------------------------------------------------------------
# 5. Cache survives across instances (disk-backed, not in-memory)
# ---------------------------------------------------------------------------


async def test_cache_persists_across_instances(tmp_path: Path) -> None:
    cache1 = Cache(dir=tmp_path, ttl_days=7)
    await cache1.set("persist", _result("https://x/"))

    cache2 = Cache(dir=tmp_path, ttl_days=7)
    got = await cache2.get("persist")
    assert got is not None
    assert got.url == "https://x/"


# ---------------------------------------------------------------------------
# 6. Corrupt cache file → treated as miss, deleted, no crash
# ---------------------------------------------------------------------------


async def test_cache_corrupt_file_treated_as_miss(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "corrupt"
    path = cache._path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")

    assert await cache.get(key) is None
    # Corrupt file is auto-cleaned
    assert not path.exists()


# ---------------------------------------------------------------------------
# 7. Default ttl_days is 7 per spec
# ---------------------------------------------------------------------------


def test_cache_default_ttl_is_seven_days(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path)
    assert cache.ttl_days == 7


# ---------------------------------------------------------------------------
# 8. Cache.set rejects non-result types
# ---------------------------------------------------------------------------


async def test_cache_set_type_check(tmp_path: Path) -> None:
    cache = Cache(dir=tmp_path)
    with pytest.raises(TypeError):
        await cache.set("k", {"not": "a ScrapeResult"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 9. skip_cache — get returns None and set is a no-op (Pack 5 rescue bug fix)
# ---------------------------------------------------------------------------


async def test_cache_get_returns_none_when_skip_cache_true(tmp_path: Path) -> None:
    """Cache.get must return None immediately when opts.skip_cache=True."""
    from scrapefold.options import ScrapeOptions

    cache = Cache(dir=tmp_path, ttl_days=7)
    # Pre-populate via a normal set so the file IS on disk.
    key = "sk_get"
    await cache.set(key, _result("https://x/"))

    opts_skip = ScrapeOptions(skip_cache=True)
    # get should bypass the disk and return None even though the file exists.
    got = await cache.get(key, opts=opts_skip)
    assert got is None, "Cache.get must return None when skip_cache=True"


async def test_cache_set_is_noop_when_skip_cache_true(tmp_path: Path) -> None:
    """Cache.set must not write to disk when opts.skip_cache=True."""
    from scrapefold.options import ScrapeOptions

    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "sk_set"
    opts_skip = ScrapeOptions(skip_cache=True)

    await cache.set(key, _result("https://x/"), opts=opts_skip)

    # File must NOT have been written.
    path = cache._path_for(key)
    assert not path.exists(), "Cache.set must not write to disk when skip_cache=True"


# ---------------------------------------------------------------------------
# 10. asyncio.to_thread — disk I/O must not block the event loop (Pack 5 rescue bug fix)
# ---------------------------------------------------------------------------


async def test_cache_get_uses_to_thread_for_disk_read(tmp_path: Path) -> None:
    """Cache.get must delegate file read to asyncio.to_thread (non-blocking)."""
    import asyncio
    from unittest.mock import patch, AsyncMock

    cache = Cache(dir=tmp_path, ttl_days=7)
    key = "thread_read"
    # Pre-populate via a normal set so the file IS on disk.
    await cache.set(key, _result("https://x/"))

    # Patch asyncio.to_thread to track calls while still executing the function.
    original_to_thread = asyncio.to_thread
    calls: list[str] = []

    async def _spy_to_thread(func: object, *args: object, **kwargs: object) -> object:
        calls.append("to_thread")
        return await original_to_thread(func, *args, **kwargs)  # type: ignore[arg-type]

    with patch("asyncio.to_thread", side_effect=_spy_to_thread):
        # Import the module-level reference used inside cache.get
        import scrapefold.cache as cache_mod
        with patch.object(cache_mod, "asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(wraps=_spy_to_thread)
            await cache.get(key)
            assert mock_asyncio.to_thread.called, "Cache.get must call asyncio.to_thread for file read"


async def test_cache_set_uses_to_thread_for_disk_write(tmp_path: Path) -> None:
    """Cache.set must delegate file write to asyncio.to_thread (non-blocking)."""
    import scrapefold.cache as cache_mod
    from unittest.mock import AsyncMock, patch

    cache = Cache(dir=tmp_path, ttl_days=7)

    with patch.object(cache_mod, "asyncio") as mock_asyncio:
        # to_thread should be called — make it a real async operation.
        import asyncio

        mock_asyncio.to_thread = AsyncMock(wraps=asyncio.to_thread)
        await cache.set("thread_write", _result("https://x/"))
        assert mock_asyncio.to_thread.called, "Cache.set must call asyncio.to_thread for file write"
