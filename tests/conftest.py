"""Shared pytest fixtures. Real fixtures (httpx-mock, local fixture HTTP server)
arrive in S2 alongside the first engine."""

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
