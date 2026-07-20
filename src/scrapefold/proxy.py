"""Session pool + proxy rotation — the "proxy over proxy" layer.

A rotation layer that sits **above** each engine's own single-proxy setting.
Today a scrapefold engine that gets blocked (403 / challenge) simply escalates
to the next, more expensive tier; it never retries the *same* tier behind a
different exit IP — which is the cheaper win for datacenter / residential
fleets. This module is that missing layer.

Modeled on `Crawlee <https://github.com/apify/crawlee>`_'s ``SessionPool``: a
set of :class:`Session` objects, each pinned to one exit proxy and carrying a
health score. A blocked outcome costs the session a strike; a clean outcome
heals one. A session past its strike budget is *retired* and never handed out
again for the rest of the pool's life.

The engine abstraction is deliberately untouched: engines still take a single
``proxy`` (unified ``ScrapeOptions.proxy``, mapped to each engine's native proxy
option). The **pool** owns *which* proxy — the router asks it for a session
before an attempt (:meth:`SessionPool.acquire`) and reports the outcome after
(:meth:`SessionPool.report`).

The pool holds no sockets and does no I/O, so it is cheap to build per walk or
to share across a whole crawl via ``extra["proxy_pool"]``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ERRORS = 3
"""Strikes a session absorbs before it is retired (Crawlee's default)."""


def _mask(proxy: str | None) -> str:
    """Render a proxy for logs without leaking embedded credentials."""
    if proxy is None:
        return "direct"
    if "@" in proxy:
        # scheme://user:pass@host:port -> scheme://***@host:port
        head, _, tail = proxy.partition("@")
        scheme = head.split("://", 1)[0] if "://" in head else ""
        return f"{scheme}://***@{tail}" if scheme else f"***@{tail}"
    return proxy


@dataclass
class Session:
    """One exit identity: a proxy plus a health score.

    ``proxy`` is the exit URL threaded into an engine's proxy option, or
    ``None`` for a direct (no-proxy) identity. ``errors`` accumulates strikes;
    the session is :attr:`retired` once it reaches ``max_errors``.
    """

    proxy: str | None
    label: str
    max_errors: int = _DEFAULT_MAX_ERRORS
    errors: int = 0
    uses: int = 0
    retired: bool = False

    @property
    def healthy(self) -> bool:
        """True while the session may still be handed out."""
        return not self.retired


class SessionPool:
    """A rotating pool of :class:`Session` identities, health-scored.

    Parameters
    ----------
    proxies:
        Exit proxy URLs (e.g. ``"http://user:pass@host:8000"``). Duplicates are
        collapsed; order is preserved. A ``None`` entry means a direct identity.
    max_errors:
        Strikes a session absorbs before retirement (default 3).
    include_direct:
        When True, add a direct (no-proxy) session in addition to ``proxies``.
        Useful when direct access sometimes works and should stay in rotation.
    """

    def __init__(
        self,
        proxies: Iterable[str | None],
        *,
        max_errors: int = _DEFAULT_MAX_ERRORS,
        include_direct: bool = False,
    ) -> None:
        self._max_errors = max(1, int(max_errors))
        self._sessions: list[Session] = []
        seen: set[str | None] = set()

        def _add(proxy: str | None) -> None:
            if proxy in seen:
                return
            seen.add(proxy)
            self._sessions.append(
                Session(proxy=proxy, label=_mask(proxy), max_errors=self._max_errors)
            )

        for proxy in proxies:
            _add(proxy)
        if include_direct:
            _add(None)

        if not self._sessions:
            # An empty pool would silently disable rotation; make it a direct
            # single-session pool so callers get predictable (if trivial) behavior.
            _add(None)

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------
    def acquire(self) -> Session | None:
        """Return the healthiest, least-used session, or ``None`` if all retired.

        Selection is deterministic: fewest errors first (healthiest), then
        fewest uses (spread load across equally-healthy exits), then insertion
        order. The chosen session's ``uses`` is incremented.
        """
        healthy = [s for s in self._sessions if s.healthy]
        if not healthy:
            logger.debug("proxy pool exhausted: all %d sessions retired", len(self._sessions))
            return None
        chosen = min(healthy, key=lambda s: (s.errors, s.uses))
        chosen.uses += 1
        return chosen

    def report(self, session: Session, *, blocked: bool) -> None:
        """Record the outcome of an attempt made with *session*.

        A ``blocked`` outcome adds a strike (retiring the session at the
        threshold); a clean outcome heals one strike so a briefly-flaky exit can
        recover rather than being retired for one bad response.
        """
        if blocked:
            session.errors += 1
            if session.errors >= session.max_errors and not session.retired:
                session.retired = True
                logger.debug(
                    "proxy pool: retiring session=%s after %d strikes",
                    session.label,
                    session.errors,
                )
            else:
                logger.debug(
                    "proxy pool: strike %d/%d for session=%s",
                    session.errors,
                    session.max_errors,
                    session.label,
                )
        elif session.errors > 0:
            session.errors -= 1
            logger.debug(
                "proxy pool: healing session=%s to %d strikes", session.label, session.errors
            )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def usable_count(self) -> int:
        """How many sessions are still eligible for rotation."""
        return sum(1 for s in self._sessions if s.healthy)

    def usable(self) -> bool:
        """True while at least one session can still be handed out."""
        return self.usable_count > 0

    def __len__(self) -> int:
        return len(self._sessions)

    def stats(self) -> dict[str, object]:
        """A small snapshot for logging / observability."""
        return {
            "total": len(self._sessions),
            "usable": self.usable_count,
            "retired": [s.label for s in self._sessions if s.retired],
        }


def build_pool_from_options(
    proxies: Iterable[str | None] | None,
    *,
    max_errors: int = _DEFAULT_MAX_ERRORS,
    include_direct: bool = False,
) -> SessionPool | None:
    """Return a :class:`SessionPool` for *proxies*, or ``None`` when there are none.

    A thin constructor the router uses to turn ``ScrapeOptions.proxies`` into a
    pool without importing rotation policy into the router itself.
    """
    materialized = [p for p in proxies] if proxies is not None else []
    if not materialized:
        return None
    return SessionPool(materialized, max_errors=max_errors, include_direct=include_direct)


__all__ = ["Session", "SessionPool", "build_pool_from_options"]
