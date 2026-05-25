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
