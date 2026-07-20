"""LLM-schema extraction over a user-provided LLM callable (ScrapeGraphAI-shaped).

`ScrapeGraphAI <https://github.com/ScrapeGraphAI/Scrapegraph-ai>`_'s hook is
"describe what you want, the LLM builds the extraction". scrapefold adopts the
*shape* but not the engine: per golden rule #5 the library never imports a
vendor LLM SDK — the caller supplies an async callable that performs the actual
inference (claude-cli subprocess, Anthropic SDK, OpenAI SDK, whatever), exactly
like ``vision.py``'s screenshot reader.

This module is pure orchestration: build a deterministic prompt from the page
content + the caller's schema, call the injected LLM, then parse and
structurally check the reply. Engines that return structured data natively
(Firecrawl ``/extract``, AnySite, Apify) still land it in ``ScrapeResult.json``
themselves; :func:`extract` generalizes that slot to *any* engine's output::

    async def my_llm(prompt: str) -> str:
        ...  # call your provider here

    result = await scrape("https://example.com/product")
    data = await extract(result, schema={"type": "object", ...}, llm=my_llm)
    # or: result = await extract_into(result, schema=..., llm=my_llm)

The schema may be a JSON-Schema-style mapping *or* a plain natural-language
description of the wanted fields (the ScrapeGraphAI style).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

TextLLMCallable = Callable[[str], Awaitable[str]]
"""Async callable with signature ``(prompt) -> str`` — golden rule #5's contract."""


class ExtractionError(ValueError):
    """The LLM reply could not be parsed / validated after all retries.

    Carries the last raw LLM reply on ``.raw_reply`` for debugging.
    """

    def __init__(self, message: str, *, raw_reply: str = "") -> None:
        super().__init__(message)
        self.raw_reply = raw_reply


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

DEFAULT_MAX_CONTENT_CHARS = 150_000
"""Default cap on page-content characters sent to the LLM (roughly 40k tokens)."""

_TRUNCATION_MARKER = "\n…[content truncated]"


def _render_schema(schema: Mapping[str, Any] | str) -> str:
    if isinstance(schema, str):
        return schema.strip()
    return json.dumps(schema, indent=2, ensure_ascii=False)


def _build_prompt(
    content: str,
    schema: Mapping[str, Any] | str,
    instructions: str | None,
) -> str:
    parts = [
        "You are a structured-data extraction engine.",
        "Extract data from the PAGE CONTENT below into a single JSON value that"
        " conforms to the SCHEMA.",
        "",
        "SCHEMA:",
        _render_schema(schema),
    ]
    if instructions:
        parts += ["", "ADDITIONAL INSTRUCTIONS:", instructions.strip()]
    parts += [
        "",
        "Rules:",
        "- Reply with ONLY the JSON value — no prose, no markdown code fences.",
        "- The top-level value must be a JSON object or array.",
        "- Use null for fields not present in the content. Never invent values.",
        "",
        "PAGE CONTENT:",
        content,
    ]
    return "\n".join(parts)


def _content_of(source: ScrapeResult | str) -> str:
    if isinstance(source, ScrapeResult):
        return source.markdown.strip() or source.text.strip()
    return source.strip()


# ---------------------------------------------------------------------------
# Reply parsing + structural checks
# ---------------------------------------------------------------------------


def _strip_code_fence(raw: str) -> str:
    """Drop a surrounding ``` / ```json fence if the reply is wrapped in one."""
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    # First line is the opening fence (``` or ```json); drop a closing fence line too.
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip()


def _parse_json_reply(raw: str) -> dict[str, Any] | list[Any]:
    """Parse an LLM reply into a JSON object/array, tolerating common wrapping.

    Tries, in order: the fence-stripped reply verbatim; then the slice from the
    first ``{``/``[`` to the last ``}``/``]`` (LLMs love a prose preamble).
    Raises ``ValueError`` when nothing parses or the top level is a scalar.
    """
    candidate = _strip_code_fence(raw)
    attempts = [candidate]
    starts = [i for i in (candidate.find("{"), candidate.find("[")) if i != -1]
    ends = [i for i in (candidate.rfind("}"), candidate.rfind("]")) if i != -1]
    if starts and ends and max(ends) > min(starts):
        attempts.append(candidate[min(starts) : max(ends) + 1])

    last_error = "empty reply"
    for attempt in attempts:
        if not attempt:
            continue
        try:
            data = json.loads(attempt)
        except json.JSONDecodeError as exc:
            last_error = str(exc)
            continue
        if isinstance(data, (dict, list)):
            return data
        last_error = f"top-level JSON value is {type(data).__name__}, expected object or array"
    raise ValueError(last_error)


def _check_against_schema(
    data: dict[str, Any] | list[Any], schema: Mapping[str, Any] | str
) -> None:
    """Cheap structural check — not full JSON-Schema validation (no new dep).

    When ``schema`` is a mapping: enforce a declared top-level ``type`` of
    ``object`` / ``array``, and top-level ``required`` keys on objects.
    Natural-language (str) schemas get no structural check.
    """
    if not isinstance(schema, Mapping):
        return
    declared = schema.get("type")
    if declared == "object" and not isinstance(data, dict):
        raise ValueError(f"schema declares type=object but reply is {type(data).__name__}")
    if declared == "array" and not isinstance(data, list):
        raise ValueError(f"schema declares type=array but reply is {type(data).__name__}")
    required = schema.get("required")
    if isinstance(data, dict) and isinstance(required, (list, tuple)):
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"reply is missing required keys: {missing}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract(
    source: ScrapeResult | str,
    *,
    schema: Mapping[str, Any] | str,
    llm: TextLLMCallable,
    instructions: str | None = None,
    max_retries: int = 1,
    max_content_chars: int | None = DEFAULT_MAX_CONTENT_CHARS,
) -> dict[str, Any] | list[Any]:
    """Extract structured data from scraped content via the injected LLM.

    Args:
        source: A :class:`ScrapeResult` (its ``markdown``, falling back to
            ``text``) or a raw markdown/text string.
        schema: JSON-Schema-style mapping, or a natural-language description of
            the wanted fields (ScrapeGraphAI style).
        llm: Async callable ``(prompt) -> str`` doing the actual inference.
            The library never imports a provider SDK (golden rule #5).
        instructions: Optional extra natural-language guidance appended to the
            prompt (e.g. "prices in USD, ignore refurbished items").
        max_retries: Re-prompts after an unparseable / schema-violating reply,
            feeding the failure reason back to the LLM. ``0`` = single shot.
        max_content_chars: Truncate the page content to this many characters
            before prompting (``None`` = never truncate).

    Returns:
        The parsed top-level JSON object or array.

    Raises:
        ValueError: If the source has no content to extract from.
        ExtractionError: If no attempt yields parseable JSON that passes the
            structural check (last raw reply on ``.raw_reply``).
    """
    content = _content_of(source)
    if not content:
        raise ValueError("source has no content to extract from")
    if max_content_chars is not None and len(content) > max_content_chars:
        logger.warning(
            "extract: content truncated %d -> %d chars before prompting",
            len(content),
            max_content_chars,
        )
        content = content[:max_content_chars] + _TRUNCATION_MARKER

    prompt = _build_prompt(content, schema, instructions)
    attempts = max(0, int(max_retries)) + 1
    raw = ""
    reason = ""
    for attempt in range(attempts):
        raw = await llm(prompt)
        try:
            data = _parse_json_reply(raw)
            _check_against_schema(data, schema)
        except ValueError as exc:
            reason = str(exc)
            logger.debug("extract: attempt %d/%d rejected: %s", attempt + 1, attempts, reason)
            # Feed the failure back so the next attempt can self-correct.
            prompt = (
                f"{prompt}\n\n"
                f"Your previous reply was rejected: {reason}\n"
                f"Previous reply:\n{raw[:2000]}\n"
                "Reply again with ONLY a valid JSON value matching the SCHEMA."
            )
            continue
        return data
    raise ExtractionError(
        f"LLM reply failed extraction after {attempts} attempt(s): {reason}",
        raw_reply=raw,
    )


async def extract_into(
    result: ScrapeResult,
    *,
    schema: Mapping[str, Any] | str,
    llm: TextLLMCallable,
    instructions: str | None = None,
    max_retries: int = 1,
    max_content_chars: int | None = DEFAULT_MAX_CONTENT_CHARS,
) -> ScrapeResult:
    """:func:`extract`, landing the data in a copy's ``ScrapeResult.json`` slot.

    Returns a new frozen :class:`ScrapeResult` with ``json`` set to the
    extracted data and ``meta["llm_extracted"] = True`` for provenance; the
    input result is untouched. Mirrors how native structured engines
    (Firecrawl ``/extract``, AnySite, Apify) populate the same slot.
    """
    data = await extract(
        result,
        schema=schema,
        llm=llm,
        instructions=instructions,
        max_retries=max_retries,
        max_content_chars=max_content_chars,
    )
    return dataclasses.replace(result, json=data, meta={**result.meta, "llm_extracted": True})


__all__ = [
    "DEFAULT_MAX_CONTENT_CHARS",
    "ExtractionError",
    "TextLLMCallable",
    "extract",
    "extract_into",
]
