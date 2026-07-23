"""Tests for BM25 focused extraction (scrapefold.focus)."""

from __future__ import annotations

from scrapefold.focus import focus_markdown, split_blocks

_DOC = """# Widget Manual

Intro paragraph about nothing in particular.

## Setup

Install the widget with the installer script.

## Pricing

The widget costs 42 dollars per month for the pro plan.

The free plan has a limit of 10 widgets.

## Support

Email us for help with billing questions.
"""


def test_split_blocks_splits_on_blank_lines() -> None:
    blocks = split_blocks(_DOC)
    assert "# Widget Manual" in blocks
    assert any("42 dollars" in b for b in blocks)


def test_focus_keeps_relevant_blocks_and_heading() -> None:
    out = focus_markdown(_DOC, "how much does the pro plan cost in dollars")
    assert "42 dollars" in out
    assert "## Pricing" in out  # governing heading attached
    assert "installer script" not in out  # irrelevant block dropped
    assert "[...]" in out  # omissions marked


def test_focus_preserves_document_order() -> None:
    out = focus_markdown(_DOC, "widgets plan limit dollars")
    assert out.index("42 dollars") < out.index("limit of 10 widgets")


def test_focus_no_match_returns_full_markdown() -> None:
    out = focus_markdown(_DOC, "zzz qqq nonexistent")
    assert out == _DOC


def test_focus_single_block_returns_unchanged() -> None:
    assert focus_markdown("just one block", "anything") == "just one block"


def test_focus_max_blocks_caps_output() -> None:
    doc = "\n\n".join(f"block about widgets number {i}" for i in range(50))
    out = focus_markdown(doc, "widgets", max_blocks=3)
    kept = [b for b in split_blocks(out) if b != "[...]"]
    assert len(kept) == 3
