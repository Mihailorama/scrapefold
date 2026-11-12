"""Cross-page boilerplate removal for crawls.

Nav bars, headers, footers and cookie banners render as the same markdown block
on most pages of a site. A block (a run of non-blank lines) that repeats on at
least half of the crawled pages is dropped from every page, which leaves each
page's main content. Needs no extra dependency and works on the output of any
engine, unlike Trafilatura-based ``main_content``.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _blocks(doc: str) -> list[str]:
    return [b.strip("\n") for b in _BLANK_LINE_RE.split(doc) if b.strip()]


def _key(block: str) -> str:
    return "\n".join(line.strip() for line in block.splitlines())


def strip_repeated_blocks(docs: list[str], min_share: float = 0.5) -> list[str]:
    """Drop blocks that occur on at least ``min_share`` of *docs* (and on 2+ docs).

    Returns one string per input doc, kept blocks joined by a blank line. A doc
    whose every block repeats is returned unchanged, so a page is never blanked.
    Fewer than two docs: returned unchanged.
    """
    if len(docs) < 2:
        return list(docs)
    split = [_blocks(d) for d in docs]
    counts = Counter(k for blocks in split for k in {_key(b) for b in blocks})
    threshold = max(2, math.ceil(len(docs) * min_share))
    out: list[str] = []
    for doc, blocks in zip(docs, split, strict=True):
        kept = [b for b in blocks if counts[_key(b)] < threshold]
        out.append("\n\n".join(kept) if kept else doc)
    return out


__all__ = ["strip_repeated_blocks"]
