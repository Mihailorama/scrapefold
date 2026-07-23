"""Focused extraction — BM25 filtering of markdown blocks.

Agents often need one fact from a long page but pay context tokens for the
whole thing. ``focus_markdown(markdown, query)`` keeps only the blocks
relevant to *query* (plus their governing headings), in original document
order. Pure stdlib — no new dependencies.

Used by the MCP ``scrape_url(focus=...)`` parameter and the CLI
``scrapefold scrape --focus`` flag. This is post-processing over the final
``ScrapeResult.markdown``; engines are unaffected.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_K1 = 1.5
_B = 0.75
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

DEFAULT_MAX_BLOCKS = 8
_RELATIVE_THRESHOLD = 0.3
"""Keep only blocks scoring at least this fraction of the best block."""


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def split_blocks(markdown: str) -> list[str]:
    """Split markdown into blocks on blank lines, preserving order."""
    blocks = re.split(r"\n\s*\n", markdown)
    return [b.strip("\n") for b in blocks if b.strip()]


def _bm25_scores(blocks: list[str], query: str) -> list[float]:
    query_terms = _tokenize(query)
    if not query_terms or not blocks:
        return [0.0] * len(blocks)

    docs = [_tokenize(b) for b in blocks]
    doc_freqs = [Counter(d) for d in docs]
    n_docs = len(docs)
    avg_len = sum(len(d) for d in docs) / n_docs

    scores = [0.0] * n_docs
    for term in set(query_terms):
        df = sum(1 for f in doc_freqs if term in f)
        if df == 0:
            continue
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
        for i, freqs in enumerate(doc_freqs):
            tf = freqs.get(term, 0)
            if tf == 0:
                continue
            denom = tf + _K1 * (1 - _B + _B * len(docs[i]) / avg_len)
            scores[i] += idf * (tf * (_K1 + 1)) / denom
    return scores


def focus_markdown(
    markdown: str,
    query: str,
    max_blocks: int = DEFAULT_MAX_BLOCKS,
) -> str:
    """Return only the markdown blocks relevant to *query*.

    Blocks are ranked with BM25; the top ``max_blocks`` scoring blocks are
    kept **in original document order**, each preceded by its nearest
    governing heading (so the excerpt keeps its structure). Omitted spans
    are marked with ``[...]``. Falls back to the full markdown when nothing
    matches — a silent empty result would be worse than a long one.
    """
    blocks = split_blocks(markdown)
    if len(blocks) <= 1:
        return markdown

    scores = _bm25_scores(blocks, query)
    best = max(scores, default=0.0)
    if best <= 0:
        return markdown

    # Relative threshold: common words (the/in/of) give many blocks a weak
    # positive score; only blocks in the same league as the best match count.
    threshold = best * _RELATIVE_THRESHOLD
    ranked = sorted(range(len(blocks)), key=lambda i: scores[i], reverse=True)
    keep = {i for i in ranked[:max_blocks] if scores[i] >= threshold}

    # Attach the nearest heading above each kept block for context.
    last_heading: int | None = None
    for i, block in enumerate(blocks):
        if block.lstrip().startswith("#"):
            last_heading = i
        elif i in keep and last_heading is not None:
            keep.add(last_heading)

    kept_sorted = sorted(keep)
    parts: list[str] = []
    prev = -1
    for i in kept_sorted:
        if prev != -1 and i > prev + 1:
            parts.append("[...]")
        parts.append(blocks[i])
        prev = i
    if kept_sorted and kept_sorted[-1] < len(blocks) - 1:
        parts.append("[...]")
    if kept_sorted and kept_sorted[0] > 0:
        parts.insert(0, "[...]")

    return "\n\n".join(parts)
