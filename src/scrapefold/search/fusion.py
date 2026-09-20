"""Reciprocal Rank Fusion (RRF) — pure arithmetic, no ML, no dependencies.

RRF merges several independently ranked result lists into one. A result at
0-based position ``i`` in an engine's list contributes ``1 / (k + i + 1)`` to
its URL's total score. URLs are deduped by a normalized key so the same page
returned by two engines is counted once, and its per-engine contributions are
kept in ``SearchResult.score_breakdown`` for explainability.

The classic reference: Cormack, Clarke & Buettcher, "Reciprocal Rank Fusion
outperforms Condorcet and individual Rank Learning Methods" (SIGIR 2009).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from scrapefold.search.types import SearchHit, SearchResult

_TRACKING_PARAMS = frozenset({"fbclid", "gclid"})


def _normalize_url(url: str) -> str:
    """Return a dedup key: lowercased scheme+host, trailing slash and tracking params dropped.

    Keeps non-tracking query params (order-preserving) so that pages that differ
    only by a real query string stay distinct, while ``utm_*`` / ``fbclid`` /
    ``gclid`` noise never splits an otherwise-identical URL into two results.
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path
    if path.endswith("/") and path != "":
        path = path[:-1]
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.lower().startswith("utm_") or key.lower() in _TRACKING_PARAMS)
    ]
    normalized = f"{scheme}://{host}{path}"
    if kept:
        normalized += f"?{urlencode(kept)}"
    return normalized


def reciprocal_rank_fusion(
    per_engine: Mapping[str, Sequence[SearchHit]],
    *,
    k: int = 60,
) -> list[SearchResult]:
    """Fuse per-engine ranked hit lists into one deduped, RRF-scored list.

    ``count`` truncation is intentionally NOT applied here — fusion returns
    every distinct URL. The public ``search()`` entrypoint truncates.
    """
    agg: dict[str, dict[str, Any]] = {}

    for engine, hits in per_engine.items():
        for position, hit in enumerate(hits):
            key = _normalize_url(hit.url)
            contribution = 1.0 / (k + position + 1)
            entry = agg.get(key)
            if entry is None:
                entry = {
                    "score": 0.0,
                    "breakdown": {},
                    "engines": set(),
                    "raw_by_engine": {},
                    "title": "",
                    "snippet": "",
                    "url": hit.url,
                    "best_position": position,
                }
                agg[key] = entry
            entry["score"] += contribution
            entry["breakdown"][engine] = entry["breakdown"].get(engine, 0.0) + contribution
            entry["engines"].add(engine)
            entry["raw_by_engine"][engine] = dict(hit.raw)
            # Canonical url is the one from the engine that ranked it highest
            # (lowest position); first-seen wins ties.
            if position < entry["best_position"]:
                entry["best_position"] = position
                entry["url"] = hit.url
            # Merge: keep the longest non-empty title / snippet across engines.
            if len(hit.title) > len(entry["title"]):
                entry["title"] = hit.title
            if len(hit.snippet) > len(entry["snippet"]):
                entry["snippet"] = hit.snippet

    pairs: list[tuple[str, SearchResult]] = [
        (
            key,
            SearchResult(
                url=entry["url"],
                title=entry["title"],
                snippet=entry["snippet"],
                score=entry["score"],
                rank=0,
                engines=tuple(sorted(entry["engines"])),
                score_breakdown=dict(entry["breakdown"]),
                raw_by_engine=entry["raw_by_engine"],
            ),
        )
        for key, entry in agg.items()
    ]

    # Sort by score desc; break ties by higher consensus, then normalized url
    # (ascending) for full determinism.
    ordered = sorted(pairs, key=lambda pair: (-pair[1].score, -pair[1].consensus, pair[0]))
    return [replace(result, rank=index) for index, (_, result) in enumerate(ordered)]


fuse = reciprocal_rank_fusion


__all__ = ["fuse", "reciprocal_rank_fusion"]
