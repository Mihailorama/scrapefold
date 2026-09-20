"""Change detection between scrapes of the same URL.

Two scrapes of one page, taken at different times, are the raw material for
monitoring — "did this page change, and how?". This module answers that with
the standard library alone (``difflib``), no new dependency and no diffing
service.

:func:`diff_results` compares two :class:`~scrapefold.result.ScrapeResult`
objects (on ``text`` by default, or ``markdown``) and returns a
:class:`ContentDiff`: a changed flag, a ``[0, 1]`` similarity ratio, the
added / removed lines, and a unified diff. :class:`SnapshotStore` persists the
latest result per URL to disk so :func:`check_for_changes` can scrape now,
compare against the stored baseline, save the new snapshot, and report what
moved — the loop a monitor runs on a schedule.

    store = SnapshotStore("~/.scrapefold/snapshots")
    diff = await check_for_changes("https://example.com/pricing", store=store)
    if diff is None:
        ...  # first run — baseline saved, nothing to compare
    elif diff.changed:
        print(diff.similarity, diff.added_lines)
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from scrapefold.cache import _result_from_dict, _result_to_dict
from scrapefold.result import ScrapeResult

if TYPE_CHECKING:
    from scrapefold.options import ScrapeOptions
    from scrapefold.pool import EnginePool

logger = logging.getLogger(__name__)

DiffField = Literal["text", "markdown"]


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContentDiff:
    """The difference between an old and a new scrape of one URL."""

    changed: bool
    similarity: float
    """Ratio in ``[0.0, 1.0]`` — ``1.0`` means the compared field is identical
    after normalization, ``0.0`` completely different (``difflib`` ratio)."""

    added_lines: list[str] = field(default_factory=list)
    """Normalized lines present in the new scrape but not the old."""

    removed_lines: list[str] = field(default_factory=list)
    """Normalized lines present in the old scrape but not the new."""

    unified_diff: str = ""
    field_compared: DiffField = "text"
    url: str = ""

    def __bool__(self) -> bool:
        """Truthy when the content changed — ``if diff:`` reads naturally."""
        return self.changed

    def as_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "similarity": self.similarity,
            "added_lines": self.added_lines,
            "removed_lines": self.removed_lines,
            "unified_diff": self.unified_diff,
            "field_compared": self.field_compared,
            "url": self.url,
        }


def _normalize_lines(text: str, *, ignore_whitespace: bool) -> list[str]:
    """Split into comparison lines, dropping blanks and (optionally) collapsing
    interior whitespace so cosmetic reflow does not read as a change."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split()) if ignore_whitespace else raw.rstrip()
        if line:
            lines.append(line)
    return lines


def diff_text(
    old: str,
    new: str,
    *,
    threshold: float = 1.0,
    ignore_whitespace: bool = True,
    field_compared: DiffField = "text",
    url: str = "",
    context_lines: int = 3,
) -> ContentDiff:
    """Diff two strings. ``changed`` is ``True`` when ``similarity < threshold``.

    ``threshold=1.0`` (the default) flags any post-normalization difference;
    lower it (e.g. ``0.98``) to ignore churn below that similarity.
    """
    old_lines = _normalize_lines(old, ignore_whitespace=ignore_whitespace)
    new_lines = _normalize_lines(new, ignore_whitespace=ignore_whitespace)

    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    similarity = matcher.ratio()

    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])

    changed = similarity < threshold
    unified = ""
    if changed:
        unified = "\n".join(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"{url} (old)" if url else "old",
                tofile=f"{url} (new)" if url else "new",
                n=context_lines,
                lineterm="",
            )
        )

    return ContentDiff(
        changed=changed,
        similarity=similarity,
        added_lines=added,
        removed_lines=removed,
        unified_diff=unified,
        field_compared=field_compared,
        url=url,
    )


def diff_results(
    old: ScrapeResult,
    new: ScrapeResult,
    *,
    on: DiffField = "text",
    threshold: float = 1.0,
    ignore_whitespace: bool = True,
    context_lines: int = 3,
) -> ContentDiff:
    """Diff two scrapes of the same URL on their ``text`` (default) or ``markdown``."""
    old_content = old.markdown if on == "markdown" else old.text
    new_content = new.markdown if on == "markdown" else new.text
    return diff_text(
        old_content,
        new_content,
        threshold=threshold,
        ignore_whitespace=ignore_whitespace,
        field_compared=on,
        url=new.url or old.url,
        context_lines=context_lines,
    )


# ---------------------------------------------------------------------------
# Snapshot store — the "previous run" baseline
# ---------------------------------------------------------------------------


class SnapshotStore:
    """On-disk store of the latest :class:`ScrapeResult` per URL.

    One JSON file per URL under ``<dir>/<first-2-of-key>/<rest>.json``, keyed by
    ``sha256(url)``. Unlike :class:`~scrapefold.cache.Cache` there is no TTL —
    a snapshot is the last-seen baseline and is overwritten on each save. Reuses
    the cache's result (de)serialization so the two stay in lockstep.
    """

    def __init__(self, dir: Path | str) -> None:
        self.dir = Path(dir).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _path_for(self, url: str) -> Path:
        key = self._key(url)
        return self.dir / key[:2] / f"{key[2:]}.json"

    async def load(self, url: str) -> ScrapeResult | None:
        """Return the stored snapshot for *url*, or ``None`` if none / unreadable."""
        path = self._path_for(url)
        try:
            raw = await asyncio.to_thread(path.read_text)
        except (FileNotFoundError, OSError):
            return None
        try:
            return _result_from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.debug("snapshot: unreadable %s (%s); removing", path, exc)
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return None

    async def save(self, result: ScrapeResult) -> None:
        """Store *result* as the latest snapshot for ``result.url`` (atomic)."""
        path = self._path_for(result.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(_result_to_dict(result), default=str)
        tmp = path.with_suffix(path.suffix + ".tmp")

        def _write() -> None:
            tmp.write_text(content)
            os.replace(tmp, path)

        await asyncio.to_thread(_write)


async def check_for_changes(
    url: str,
    opts: ScrapeOptions | None = None,
    *,
    store: SnapshotStore,
    on: DiffField = "text",
    threshold: float = 1.0,
    ignore_whitespace: bool = True,
    pool: EnginePool | None = None,
) -> ContentDiff | None:
    """Scrape *url* now, diff it against the stored snapshot, save the new one.

    Returns ``None`` on the first run for a URL (no baseline yet — the snapshot
    is saved for next time), otherwise a :class:`ContentDiff`. The scrape uses
    the normal router via :func:`scrapefold.scrape`.
    """
    from scrapefold import scrape

    previous = await store.load(url)
    current = await scrape(url, opts, pool=pool)
    await store.save(current)
    if previous is None:
        return None
    return diff_results(
        previous,
        current,
        on=on,
        threshold=threshold,
        ignore_whitespace=ignore_whitespace,
    )


__all__ = [
    "ContentDiff",
    "DiffField",
    "SnapshotStore",
    "check_for_changes",
    "diff_results",
    "diff_text",
]
