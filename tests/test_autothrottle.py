"""Tests for AutoThrottle — Scrapy-style adaptive per-host crawl delay.

Mostly pure-unit tests of the controller (no I/O): the latency-EWMA delay
adaptation, the never-speed-up-on-error rule, the 429/503 exponential backoff,
the ``[min,max]`` clamp, and per-host isolation. Plus one offline crawler
integration test proving ``opts.autothrottle`` wires the controller into the
walk (delay sleeps + latency/status recording).
"""

from __future__ import annotations

import typing
from pathlib import Path

import pytest

import scrapefold
from scrapefold import ScrapeOptions, ScrapeResult
from scrapefold.crawler.throttle import AutoThrottle, host_of

H = "example.com"


def test_host_of_extracts_lowercase_host() -> None:
    assert host_of("https://Example.COM/a/b?q=1") == "example.com"
    assert host_of("http://sub.example.com:8080/") == "sub.example.com"
    assert host_of("not a url") == ""


def test_start_delay_used_before_any_sample() -> None:
    t = AutoThrottle(start_delay=2.5)
    assert t.delay_for(H) == 2.5


def test_delay_eases_toward_latency_over_target_concurrency() -> None:
    # target_concurrency=1 → target_delay == ewma_latency. First sample seeds
    # the EWMA, so new_delay = (start_delay + latency) / 2.
    t = AutoThrottle(start_delay=1.0, target_concurrency=1.0)
    t.record(H, latency_s=3.0, status_code=200)
    assert t.delay_for(H) == pytest.approx((1.0 + 3.0) / 2.0)  # 2.0


def test_higher_target_concurrency_shortens_delay() -> None:
    # target_delay = latency / target_concurrency → larger concurrency ⇒ smaller.
    fast = AutoThrottle(start_delay=1.0, target_concurrency=4.0)
    fast.record(H, latency_s=4.0, status_code=200)
    slow = AutoThrottle(start_delay=1.0, target_concurrency=1.0)
    slow.record(H, latency_s=4.0, status_code=200)
    assert fast.delay_for(H) < slow.delay_for(H)


def test_autothrottle_backs_off_on_rising_latency() -> None:
    """The named acceptance test: as observed latency climbs, the per-host
    delay rises monotonically and effective throughput falls."""
    t = AutoThrottle(start_delay=1.0, target_concurrency=1.0, ewma_alpha=1.0)

    delays: list[float] = []
    throughputs: list[float] = []
    for latency in (2.0, 4.0, 8.0, 16.0, 32.0):
        t.record(H, latency_s=latency, status_code=200)
        delays.append(t.delay_for(H))
        throughputs.append(t.effective_throughput(H))

    assert delays == sorted(delays), f"delay did not rise monotonically: {delays}"
    assert delays[-1] > delays[0]
    assert throughputs == sorted(throughputs, reverse=True), (
        f"throughput did not fall monotonically: {throughputs}"
    )


def test_never_speeds_up_on_error_status() -> None:
    t = AutoThrottle(start_delay=5.0, target_concurrency=1.0, ewma_alpha=1.0)
    # A fast-returning 500 (low latency) would otherwise pull the delay down.
    t.record(H, latency_s=0.1, status_code=500)
    assert t.delay_for(H) >= 5.0


def test_429_backs_off_hard_even_when_fast() -> None:
    t = AutoThrottle(start_delay=2.0, target_concurrency=1.0)
    t.record(H, latency_s=0.05, status_code=429)
    # Explicit rate-limit → at least doubles the previous delay.
    assert t.delay_for(H) >= 4.0


def test_503_backs_off_hard() -> None:
    t = AutoThrottle(start_delay=2.0)
    t.record(H, latency_s=0.05, status_code=503)
    assert t.delay_for(H) >= 4.0


def test_failed_flag_backs_off_without_latency_or_status() -> None:
    t = AutoThrottle(start_delay=2.0)
    before = t.delay_for(H)
    t.record(H, latency_s=None, status_code=None, failed=True)
    assert t.delay_for(H) >= before * 2.0


def test_none_latency_leaves_ewma_untouched() -> None:
    t = AutoThrottle(start_delay=1.0, target_concurrency=1.0, ewma_alpha=1.0)
    t.record(H, latency_s=10.0, status_code=200)  # seeds ewma=10
    snap_before = t.snapshot()[H]["ewma_latency"]
    t.record(H, latency_s=None, status_code=None, failed=True)  # no sample
    assert t.snapshot()[H]["ewma_latency"] == snap_before


def test_max_delay_clamp() -> None:
    t = AutoThrottle(start_delay=1.0, max_delay=3.0, ewma_alpha=1.0)
    for _ in range(10):
        t.record(H, latency_s=100.0, status_code=503)
    assert t.delay_for(H) == 3.0


def test_min_delay_clamp() -> None:
    t = AutoThrottle(start_delay=1.0, min_delay=0.5, target_concurrency=100.0, ewma_alpha=1.0)
    for _ in range(20):
        t.record(H, latency_s=0.001, status_code=200)
    assert t.delay_for(H) == pytest.approx(0.5)


def test_per_host_isolation() -> None:
    t = AutoThrottle(start_delay=1.0, ewma_alpha=1.0)
    t.record("slow.example", latency_s=10.0, status_code=503)
    t.record("fast.example", latency_s=0.1, status_code=200)
    assert t.delay_for("slow.example") > t.delay_for("fast.example")


def test_effective_throughput_of_unknown_host() -> None:
    t = AutoThrottle(start_delay=2.0)
    assert t.effective_throughput("never.seen") == pytest.approx(0.5)


def test_effective_throughput_infinite_when_zero_delay() -> None:
    t = AutoThrottle(start_delay=0.0)
    assert t.effective_throughput("never.seen") == float("inf")


def test_snapshot_reports_per_host_state() -> None:
    t = AutoThrottle(start_delay=1.0, ewma_alpha=1.0)
    t.record(H, latency_s=2.0, status_code=200)
    snap = t.snapshot()
    assert set(snap[H]) == {"delay", "ewma_latency"}
    assert snap[H]["ewma_latency"] == pytest.approx(2.0)


# --- crawler integration (offline) ---------------------------------------


@pytest.fixture
def stub_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_discover(root: str, *, max_urls: int, **_kwargs: object) -> list[str]:
        return [
            "https://example.com/",
            "https://example.com/a",
            "https://example.com/b",
        ][:max_urls]

    monkeypatch.setattr("scrapefold.crawler.sitemap.discover_urls", _fake_discover)


@pytest.fixture
def stub_head_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class _Resp:
        status_code = 200
        headers: typing.ClassVar[dict[str, str]] = {}

    async def _fake_head(self: httpx.AsyncClient, url: str, **_kw: object) -> _Resp:
        return _Resp()

    monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)


async def test_crawl_wires_autothrottle_delays_and_records(
    tmp_path: Path,
    stub_discover: None,
    stub_head_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``autothrottle=True`` the crawl sleeps the per-host delay before each
    fetch and folds each response's latency/status back into the controller."""
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def _fake_scrape(
        url: str, opts: ScrapeOptions | None = None, **kw: object
    ) -> ScrapeResult:
        return ScrapeResult(
            url=url,
            text=f"text-of-{url}",
            markdown=f"# {url}",
            html=None,
            engine="stub",
            elapsed_ms=3000,  # 3s latency → drives the delay up from start
            meta={"status_code": 200},
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.crawler.asyncio.sleep", _fake_sleep)

    out = tmp_path / "crawl.md"
    result = await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=3, autothrottle=True),
        output=out,
    )

    assert len(result.pages) == 3
    # One sleep per fetched URL.
    assert len(sleeps) == 3
    # First sleep is the start_delay (1.0); later sleeps ease toward the 3s
    # observed latency, so the delay must rise across the walk.
    assert sleeps[0] == pytest.approx(1.0)
    assert sleeps[-1] > sleeps[0]


async def test_crawl_without_autothrottle_never_sleeps(
    tmp_path: Path,
    stub_discover: None,
    stub_head_ok: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def _fake_scrape(
        url: str, opts: ScrapeOptions | None = None, **kw: object
    ) -> ScrapeResult:
        return ScrapeResult(
            url=url, text="t", markdown="# t", html=None, engine="stub", elapsed_ms=1
        )

    monkeypatch.setattr("scrapefold.scrape", _fake_scrape)
    monkeypatch.setattr("scrapefold.crawler.scrape", _fake_scrape, raising=False)
    monkeypatch.setattr("scrapefold.crawler.asyncio.sleep", _fake_sleep)

    out = tmp_path / "crawl.md"
    await scrapefold.crawl_site(
        "https://example.com/",
        opts=ScrapeOptions(max_pages=3, autothrottle=False),
        output=out,
    )
    assert sleeps == []
