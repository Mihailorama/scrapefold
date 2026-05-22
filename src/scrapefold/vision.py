"""Vision utilities for analyzing page screenshots via a user-provided LLM callable.

scrapefold never imports vendor LLM SDKs directly. Instead, callers supply an
async callable that performs the actual LLM inference. This module orchestrates
argument validation, prompt construction, and response parsing around that callable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

LLMCallable = Callable[[str, bytes, str], Awaitable[str]]
"""Async callable with signature ``(prompt, image_bytes, mime_type) -> str``."""

_VALID_MIME_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/webp"})

DEFAULT_PROMPT = (
    "Describe this webpage screenshot. If it shows an anti-bot challenge, captcha, "
    "or error page, say so explicitly. Otherwise summarize the main content."
)

_ANTIBOT_PROMPT = (
    "Look at this webpage screenshot. Does it show an anti-bot challenge, captcha, "
    "Cloudflare verification, 'verify you are human' page, or any other bot-detection "
    "barrier? Reply with exactly one word on the first line: BLOCKED if yes, OK if no. "
    "Then optionally explain."
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_inputs(
    image_bytes: bytes,
    mime_type: str,
) -> None:
    """Raise ValueError for invalid inputs before touching the LLM."""
    if not image_bytes:
        raise ValueError("image_bytes is empty")
    if mime_type not in _VALID_MIME_TYPES:
        raise ValueError(
            f"mime_type {mime_type!r} is not supported; expected one of {sorted(_VALID_MIME_TYPES)}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze_screenshot_with_llm(
    image_bytes: bytes,
    llm_callable: LLMCallable,
    *,
    prompt: str = DEFAULT_PROMPT,
    mime_type: Literal["image/png", "image/jpeg", "image/webp"] = "image/png",
) -> str:
    """Analyze a screenshot via a user-provided async LLM callable.

    Returns the LLM's text response verbatim. Raises ``ValueError`` on empty
    input or unsupported MIME type; propagates the callable's own exceptions
    unchanged so the caller can decide retry behavior.

    Args:
        image_bytes: Raw image data. Must not be empty.
        llm_callable: Async callable with signature
            ``(prompt: str, image_bytes: bytes, mime_type: str) -> str``.
        prompt: Instruction sent to the LLM. Defaults to ``DEFAULT_PROMPT``.
        mime_type: MIME type of the image. One of ``image/png``, ``image/jpeg``,
            or ``image/webp``.

    Returns:
        The LLM's text response.

    Raises:
        ValueError: If ``image_bytes`` is empty or ``mime_type`` is not supported.
    """
    _validate_inputs(image_bytes, mime_type)
    logger.debug(
        "analyze_screenshot_with_llm: sending %d bytes (%s) to LLM callable",
        len(image_bytes),
        mime_type,
    )
    response = await llm_callable(prompt, image_bytes, mime_type)
    logger.debug("analyze_screenshot_with_llm: received response (%d chars)", len(response))
    return response


async def detect_antibot_in_screenshot(
    image_bytes: bytes,
    llm_callable: LLMCallable,
    *,
    mime_type: Literal["image/png", "image/jpeg", "image/webp"] = "image/png",
) -> bool:
    """Detect whether a screenshot shows an anti-bot challenge page.

    Asks the LLM to respond with ``BLOCKED`` or ``OK`` as the first word.
    Returns ``True`` if the first word of the response is ``BLOCKED``
    (case-insensitive). Returns ``False`` for empty, whitespace-only, or any
    other first word.

    Args:
        image_bytes: Raw image data. Must not be empty.
        llm_callable: Async callable with signature
            ``(prompt: str, image_bytes: bytes, mime_type: str) -> str``.
        mime_type: MIME type of the image.

    Returns:
        ``True`` if the screenshot shows an anti-bot challenge, else ``False``.

    Raises:
        ValueError: If ``image_bytes`` is empty or ``mime_type`` is not supported.
    """
    _validate_inputs(image_bytes, mime_type)
    logger.debug(
        "detect_antibot_in_screenshot: sending %d bytes (%s) to LLM callable",
        len(image_bytes),
        mime_type,
    )
    response = await llm_callable(_ANTIBOT_PROMPT, image_bytes, mime_type)
    logger.debug("detect_antibot_in_screenshot: received response (%d chars)", len(response))

    stripped = response.strip()
    if not stripped:
        return False

    first_word = stripped.split()[0].lower()
    return first_word == "blocked"


__all__ = [
    "DEFAULT_PROMPT",
    "LLMCallable",
    "analyze_screenshot_with_llm",
    "detect_antibot_in_screenshot",
]
