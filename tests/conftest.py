"""Shared pytest fixtures for the offline test suite."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_html() -> str:
    """A tiny HTML document used by html-to-text and engine tests."""
    return (
        "<html><head><title>Example</title></head>"
        "<body><h1>Hello</h1><p>World</p>"
        '<a href="/about">about</a></body></html>'
    )


@pytest.fixture
def fake_png_bytes() -> bytes:
    """Minimal valid-looking PNG header + padding, for screenshot tests."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
