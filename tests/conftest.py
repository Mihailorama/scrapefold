"""Shared pytest fixtures for the offline test suite."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def make_scrapling_response(html: str, status: int = 200) -> MagicMock:
    """Factory for a fake scrapling Response object used by both scrapling engines.

    Each scrapling fetcher returns a Response whose ``html_content`` stringifies
    to the raw HTML. The same shape is needed by ``test_engine_scrapling_stealth``
    and ``test_engine_scrapling_fast``, hence the shared factory.
    """
    resp = MagicMock()
    resp.status = status
    resp.html_content = MagicMock()
    resp.html_content.__str__ = lambda self: html
    return resp


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
