"""Cross-page boilerplate removal for crawls.

Nav bars, headers, footers and cookie banners render as the same markdown block
on most pages of a site. A block (a run of non-blank lines) that repeats on at
least half of the crawled pages is dropped when it sits in the leading or
trailing run of a page, which leaves each page's main content. Needs no extra dependency and works on the output of any
engine, unlike Trafilatura-based ``main_content``.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _blocks(doc: str) -> list[str]:
    """Split on blank lines, but never inside a fenced code block."""
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in doc.replace("\r\n", "\n").split("\n"):
        mark = _FENCE_RE.match(line)
        if mark:
            fence = None if fence == mark.group(1) else (fence or mark.group(1))
        if fence is None and not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _key(block: str) -> str:
    return "\n".join(line.strip() for line in block.splitlines())


MIN_DOCS = 3


def strip_repeated_blocks(docs: list[str], min_share: float = 0.5) -> list[str]:
    """Drop the page chrome: repeated blocks at the start and end of each page.

    A block repeats when it occurs on at least ``min_share`` of *docs* (and on
    2+ docs). Only the leading and trailing runs of repeated blocks are removed;
    the first non-repeated block from either end stops the run, so a heading or
    paragraph that repeats inside the content (``## Parameters``) stays. Code
    fences are never split. A doc whose every block repeats is returned
    unchanged, so a page is never blanked. Fewer than ``MIN_DOCS`` docs are
    returned unchanged: two pages cannot tell chrome from shared content.
    """
    if len(docs) < MIN_DOCS:
        return list(docs)
    split = [_blocks(d) for d in docs]
    counts = Counter(k for blocks in split for k in {_key(b) for b in blocks})
    threshold = max(2, math.ceil(len(docs) * min_share))
    out: list[str] = []
    for doc, blocks in zip(docs, split, strict=True):
        repeated = [counts[_key(b)] >= threshold for b in blocks]
        lo, hi = 0, len(blocks)
        while lo < hi and repeated[lo]:
            lo += 1
        while hi > lo and repeated[hi - 1]:
            hi -= 1
        kept = blocks[lo:hi]
        out.append("\n\n".join(kept) if kept else doc)
    return out


__all__ = ["MIN_DOCS", "strip_repeated_blocks"]
