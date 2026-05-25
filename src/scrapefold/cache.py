"""Disk-backed TTL cache for ScrapeResult.

Key derivation: sha256(url) + sha256(canonical-json(opts)). One file per
key under ``<cache_dir>/<first-2-of-key>/<rest-of-key>.json``. TTL via
file mtime. Atomic writes via ``os.replace``. Corrupt files are treated
as misses and removed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from scrapefold.options import ScrapeOptions
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_TTL_DAYS = 7


_PRIMITIVES: tuple[type, ...] = (str, int, float, bool, type(None))


def _canonicalize(obj: Any) -> Any:
    """Recursively transform *obj* into a JSON-serializable, dict-key-sorted form.

    Strict by design (Codex round-1 HIGH #3): unknown types raise
    ``ValueError`` so the caller can bypass the cache with a logged
    warning rather than producing a non-deterministic key via
    ``default=str``.
    """
    if isinstance(obj, _PRIMITIVES):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(x) for x in obj]
    if isinstance(obj, (set, frozenset)):
        items = [_canonicalize(x) for x in obj]
        try:
            return sorted(items)
        except TypeError as exc:
            raise ValueError(f"set with mixed-comparable elements: {exc}") from exc
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k in sorted(obj):
            if not isinstance(k, str):
                raise ValueError(f"non-string dict key: {k!r} ({type(k).__name__})")
            out[k] = _canonicalize(obj[k])
        return out
    if is_dataclass(obj) and not isinstance(obj, type):
        return _canonicalize(asdict(obj))
    raise ValueError(f"opts contains non-canonicalizable type: {type(obj).__name__}")


def _canonical_opts(opts: ScrapeOptions) -> str:
    """Produce a stable JSON string for opts. Raises ``ValueError`` on failure."""
    return json.dumps(_canonicalize(asdict(opts)), sort_keys=True)


def make_key(url: str, opts: ScrapeOptions) -> str | None:
    """Return the 64-char hex sha256 key for (url, opts), or ``None`` if opts
    contains a non-canonicalizable value (cache must bypass).
    """
    try:
        canonical = _canonical_opts(opts)
    except ValueError as exc:
        logger.warning(
            "cache: opts not canonicalizable (%s); cache will bypass for url=%s",
            exc,
            url,
        )
        return None
    h = hashlib.sha256()
    h.update(url.encode("utf-8"))
    h.update(b"\x00")
    h.update(canonical.encode("utf-8"))
    return h.hexdigest()


class Cache:
    """sha256-keyed disk cache for ScrapeResult, TTL'd via file mtime."""

    def __init__(self, dir: Path | str, ttl_days: int = _DEFAULT_TTL_DAYS) -> None:
        self.dir = Path(dir)
        self.ttl_days = ttl_days
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        # Sharded by first two hex chars to avoid one giant flat directory.
        return self.dir / key[:2] / f"{key[2:]}.json"

    async def get(self, key: str) -> ScrapeResult | None:
        path = self._path_for(key)
        if not path.exists():
            return None

        # TTL via mtime
        age_s = time.time() - path.stat().st_mtime
        if age_s > self.ttl_days * 86400:
            return None

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("cache: corrupt file %s (%s); removing", path, exc)
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return None

        try:
            return _result_from_dict(data)
        except (KeyError, TypeError) as exc:
            logger.debug("cache: shape mismatch in %s (%s); removing", path, exc)
            path.unlink(missing_ok=True)
            return None

    async def set(self, key: str, result: ScrapeResult) -> None:
        if not isinstance(result, ScrapeResult):
            raise TypeError(f"Cache.set expects ScrapeResult, got {type(result).__name__}")

        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(_result_to_dict(result), default=str))
        os.replace(tmp, path)  # atomic rename


def _result_to_dict(r: ScrapeResult) -> dict[str, Any]:
    return asdict(r)


def _result_from_dict(d: dict[str, Any]) -> ScrapeResult:
    # Be strict about the required fields; tolerate missing optional ones
    return ScrapeResult(
        url=d["url"],
        text=d["text"],
        markdown=d["markdown"],
        html=d.get("html"),
        json=d.get("json"),
        screenshot_b64=d.get("screenshot_b64"),
        engine=d["engine"],
        elapsed_ms=d["elapsed_ms"],
        cost_usd=d.get("cost_usd", 0.0),
        meta=d.get("meta", {}),
        failures=d.get("failures", []),
    )


__all__ = ["Cache", "make_key"]
