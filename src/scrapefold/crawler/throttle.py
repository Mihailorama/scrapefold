"""AutoThrottle — adaptive per-host politeness for the crawl loop.

A port of `Scrapy <https://github.com/scrapy/scrapy>`_'s AutoThrottle idea:
adapt the inter-request delay to the *observed* server latency, so the crawler
speeds up on healthy hosts and backs off when a host slows down or starts
returning rate-limit / overload codes. Without this a large crawl fans out at a
fixed rate and can hammer a slow origin — inviting exactly the blocks the
stealth engines were added to avoid.

Algorithm (per remote host, Scrapy-faithful):

* Keep an EWMA of response latency.
* ``target_delay = ewma_latency / target_concurrency`` — the delay that would
  keep roughly ``target_concurrency`` requests in flight to that host.
* ``new_delay = (current_delay + target_delay) / 2`` — ease toward the target
  rather than snapping, so one slow response doesn't overreact.
* Never *decrease* the delay on a non-2xx response (don't reward errors with a
  faster crawl), and apply an explicit exponential backoff on rate-limit /
  overload codes (429 / 503).
* Clamp to ``[min_delay, max_delay]``.

The controller holds no sockets and does no I/O — the crawl loop sleeps
``delay_for(host)`` before a request and calls ``record(host, …)`` after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BACKOFF_STATUS = frozenset({429, 503})


def host_of(url: str) -> str:
    """Return the lower-case host for *url* (``""`` when unparseable)."""
    return (urlparse(url).hostname or "").lower()


@dataclass
class _HostState:
    """Per-host throttle state: the current delay and the smoothed latency."""

    delay: float
    ewma_latency: float | None = None


class AutoThrottle:
    """Adaptive per-host request delay with latency EWMA + 429/503 backoff.

    Parameters
    ----------
    target_concurrency:
        Desired average number of in-flight requests per host. Higher → shorter
        delays (more aggressive). Scrapy's default is 1.0.
    start_delay:
        Delay used for a host before any response has been observed.
    max_delay / min_delay:
        Hard clamps on the adapted delay (seconds).
    ewma_alpha:
        Smoothing factor for the latency EWMA in ``(0, 1]``; higher reacts
        faster to the latest sample.
    """

    def __init__(
        self,
        *,
        target_concurrency: float = 1.0,
        start_delay: float = 1.0,
        max_delay: float = 60.0,
        min_delay: float = 0.0,
        ewma_alpha: float = 0.5,
    ) -> None:
        self._target_concurrency = max(0.01, float(target_concurrency))
        self._start_delay = max(0.0, float(start_delay))
        self._max_delay = max(0.0, float(max_delay))
        self._min_delay = max(0.0, float(min_delay))
        self._alpha = min(1.0, max(0.01, float(ewma_alpha)))
        self._hosts: dict[str, _HostState] = {}

    def _state(self, host: str) -> _HostState:
        st = self._hosts.get(host)
        if st is None:
            st = _HostState(delay=self._clamp(self._start_delay))
            self._hosts[host] = st
        return st

    def _clamp(self, delay: float) -> float:
        return min(self._max_delay, max(self._min_delay, delay))

    def delay_for(self, host: str) -> float:
        """Seconds to wait before the next request to *host*."""
        return self._state(host).delay

    def record(
        self,
        host: str,
        *,
        latency_s: float | None,
        status_code: int | None,
        failed: bool = False,
    ) -> None:
        """Fold one observed (latency, status) sample into *host*'s delay.

        ``latency_s`` may be ``None`` when no timing is available (e.g. the
        fetch raised before a response) — the latency EWMA is then left
        untouched and only the delay-adjustment rules apply. ``failed=True``
        marks a hard fetch failure (engine ladder exhausted): treated like an
        overload for back-off purposes even without a status code.
        """
        st = self._state(host)

        if latency_s is not None:
            latency = max(0.0, float(latency_s))
            st.ewma_latency = (
                latency
                if st.ewma_latency is None
                else self._alpha * latency + (1.0 - self._alpha) * st.ewma_latency
            )

        # target_delay eases toward keeping ~target_concurrency in flight; with
        # no latency sample yet, fall back to the current delay (a no-op ease).
        basis = st.ewma_latency if st.ewma_latency is not None else st.delay
        target_delay = basis / self._target_concurrency
        new_delay = (st.delay + target_delay) / 2.0

        is_error = failed or (status_code is not None and status_code >= 400)
        if is_error:
            # Never speed up on an error response.
            new_delay = max(st.delay, new_delay)
        if failed or status_code in _BACKOFF_STATUS:
            # Explicit rate-limit / overload (or a hard failure) — back off hard
            # even if the error came back fast (a quick 429 must not shorten it).
            new_delay = max(new_delay, (st.delay or self._start_delay) * 2.0)

        st.delay = self._clamp(new_delay)
        logger.debug(
            "autothrottle: host=%s latency=%s status=%s failed=%s -> delay=%.3f",
            host,
            latency_s,
            status_code,
            failed,
            st.delay,
        )

    def effective_throughput(self, host: str) -> float:
        """Requests/sec implied by the current delay + smoothed latency.

        A monotone "how fast are we crawling this host" gauge: as latency (and
        therefore delay) rises, throughput falls. Useful for observability and
        for asserting back-off behavior.
        """
        st = self._hosts.get(host)
        if st is None:
            return float("inf") if self._start_delay == 0 else 1.0 / self._start_delay
        per_request = st.delay + (st.ewma_latency or 0.0)
        return float("inf") if per_request == 0 else 1.0 / per_request

    def snapshot(self) -> dict[str, dict[str, float | None]]:
        """A per-host view of ``{delay, ewma_latency}`` for logging."""
        return {
            host: {"delay": st.delay, "ewma_latency": st.ewma_latency}
            for host, st in self._hosts.items()
        }


__all__ = ["AutoThrottle", "host_of"]
