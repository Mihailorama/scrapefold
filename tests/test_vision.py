"""Tests for scrapefold.vision — analyze_screenshot_with_llm and detect_antibot_in_screenshot."""

from __future__ import annotations

import pytest

from scrapefold.vision import (
    DEFAULT_PROMPT,
    analyze_screenshot_with_llm,
    detect_antibot_in_screenshot,
)

# ---------------------------------------------------------------------------
# Async fake callables used across tests
# ---------------------------------------------------------------------------


async def fake_llm_ok(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    return "OK page looks normal"


async def fake_llm_blocked(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    return "BLOCKED captcha is visible on the page"


async def fake_llm_blocked_mixed_case(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    return "Blocked by Cloudflare verification challenge"


async def fake_llm_empty(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    return ""


async def fake_llm_whitespace(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    return "   \n\t  "


async def fake_llm_capture(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    """Stores received args in a module-level dict for inspection."""
    _captured["prompt"] = prompt
    _captured["image_bytes"] = image_bytes
    _captured["mime_type"] = mime_type
    return "captured response"


async def fake_llm_raises(prompt: str, image_bytes: bytes, mime_type: str) -> str:
    raise ValueError("LLM service unavailable")


_captured: dict = {}


SAMPLE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # minimal fake PNG bytes


# ---------------------------------------------------------------------------
# analyze_screenshot_with_llm
# ---------------------------------------------------------------------------


class TestAnalyzeScreenshot:
    async def test_passes_prompt_bytes_mime_to_callable(self) -> None:
        _captured.clear()
        await analyze_screenshot_with_llm(SAMPLE_BYTES, fake_llm_capture, mime_type="image/png")
        assert _captured["image_bytes"] == SAMPLE_BYTES
        assert _captured["mime_type"] == "image/png"
        assert isinstance(_captured["prompt"], str)
        assert len(_captured["prompt"]) > 0

    async def test_returns_callable_response_verbatim(self) -> None:
        result = await analyze_screenshot_with_llm(SAMPLE_BYTES, fake_llm_ok)
        assert result == "OK page looks normal"

    async def test_raises_value_error_on_empty_bytes(self) -> None:
        with pytest.raises(ValueError, match="image_bytes is empty"):
            await analyze_screenshot_with_llm(b"", fake_llm_ok)

    async def test_raises_value_error_on_invalid_mime_type(self) -> None:
        with pytest.raises(ValueError, match="mime_type"):
            await analyze_screenshot_with_llm(
                SAMPLE_BYTES,
                fake_llm_ok,
                mime_type="image/gif",  # type: ignore[arg-type]
            )

    async def test_default_prompt_is_used_when_none_provided(self) -> None:
        _captured.clear()
        await analyze_screenshot_with_llm(SAMPLE_BYTES, fake_llm_capture)
        assert _captured["prompt"] == DEFAULT_PROMPT

    async def test_custom_prompt_overrides_default(self) -> None:
        _captured.clear()
        custom = "Is this a login page?"
        await analyze_screenshot_with_llm(SAMPLE_BYTES, fake_llm_capture, prompt=custom)
        assert _captured["prompt"] == custom

    async def test_propagates_exception_from_callable(self) -> None:
        with pytest.raises(ValueError, match="LLM service unavailable"):
            await analyze_screenshot_with_llm(SAMPLE_BYTES, fake_llm_raises)

    async def test_jpeg_mime_type_accepted(self) -> None:
        result = await analyze_screenshot_with_llm(
            SAMPLE_BYTES, fake_llm_ok, mime_type="image/jpeg"
        )
        assert result == "OK page looks normal"

    async def test_webp_mime_type_accepted(self) -> None:
        result = await analyze_screenshot_with_llm(
            SAMPLE_BYTES, fake_llm_ok, mime_type="image/webp"
        )
        assert result == "OK page looks normal"

    async def test_mime_type_passed_to_callable(self) -> None:
        _captured.clear()
        await analyze_screenshot_with_llm(SAMPLE_BYTES, fake_llm_capture, mime_type="image/jpeg")
        assert _captured["mime_type"] == "image/jpeg"


# ---------------------------------------------------------------------------
# detect_antibot_in_screenshot
# ---------------------------------------------------------------------------


class TestDetectAntibot:
    async def test_returns_true_for_blocked_response(self) -> None:
        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, fake_llm_blocked)
        assert result is True

    async def test_returns_true_for_blocked_mixed_case(self) -> None:
        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, fake_llm_blocked_mixed_case)
        assert result is True

    async def test_returns_false_for_ok_response(self) -> None:
        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, fake_llm_ok)
        assert result is False

    async def test_returns_false_for_empty_response(self) -> None:
        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, fake_llm_empty)
        assert result is False

    async def test_returns_false_for_whitespace_response(self) -> None:
        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, fake_llm_whitespace)
        assert result is False

    async def test_raises_on_empty_bytes(self) -> None:
        with pytest.raises(ValueError, match="image_bytes is empty"):
            await detect_antibot_in_screenshot(b"", fake_llm_blocked)

    async def test_propagates_exception_from_callable(self) -> None:
        with pytest.raises(ValueError, match="LLM service unavailable"):
            await detect_antibot_in_screenshot(SAMPLE_BYTES, fake_llm_raises)

    async def test_blocked_uppercase_is_detected(self) -> None:
        async def llm_upper(p: str, img: bytes, mime: str) -> str:
            return "BLOCKED all caps response"

        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, llm_upper)
        assert result is True

    async def test_blocked_lowercase_is_detected(self) -> None:
        async def llm_lower(p: str, img: bytes, mime: str) -> str:
            return "blocked by cloudflare"

        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, llm_lower)
        assert result is True

    async def test_non_blocked_first_word_is_false(self) -> None:
        async def llm_other(p: str, img: bytes, mime: str) -> str:
            return "CAPTCHA detected but not explicitly blocked"

        result = await detect_antibot_in_screenshot(SAMPLE_BYTES, llm_other)
        assert result is False
