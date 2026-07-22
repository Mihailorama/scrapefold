"""Tests for LLM-schema extraction over the injected user LLM callable.

Pure offline unit tests: the "LLM" is always a stub async callable, per golden
rule #5 (no vendor LLM SDK anywhere in the library). Covers the named
``test_extract_fills_json_via_injected_llm`` acceptance test plus prompt
construction, lenient reply parsing, the self-correcting retry loop, the cheap
structural schema check, truncation, and the frozen-result ``extract_into``
copy semantics.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from scrapefold import ExtractionError, ScrapeResult, extract, extract_into
from scrapefold.extract import _TRUNCATION_MARKER, DEFAULT_MAX_CONTENT_CHARS

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "price": {"type": "number"}},
    "required": ["title", "price"],
}

BLOB = {"title": "Widget", "price": 9.99}


def make_result(markdown: str = "# Widget\nPrice: $9.99", text: str = "") -> ScrapeResult:
    return ScrapeResult(
        url="https://shop.example/widget",
        text=text or markdown,
        markdown=markdown,
        html=None,
        engine="stub",
        elapsed_ms=1,
    )


def make_llm(*replies: str) -> tuple[Any, list[str]]:
    """Stub LLM returning canned replies in order; records every prompt."""
    prompts: list[str] = []
    queue = list(replies)

    async def _llm(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0) if queue else replies[-1]

    return _llm, prompts


# --- the named acceptance test --------------------------------------------


async def test_extract_fills_json_via_injected_llm() -> None:
    """Stub llm returns a fixed JSON blob; it lands in ``result.json`` — and no
    vendor LLM module was imported anywhere in the process."""
    llm, _ = make_llm(json.dumps(BLOB))
    result = make_result()
    assert result.json is None

    extracted = await extract_into(result, schema=SCHEMA, llm=llm)

    assert extracted.json == BLOB
    assert extracted.meta["llm_extracted"] is True
    # The input result is frozen and untouched.
    assert result.json is None
    assert "llm_extracted" not in result.meta
    # Golden rule #5: the library must never import a provider SDK.
    for vendor in ("openai", "anthropic", "litellm", "google.generativeai"):
        assert vendor not in sys.modules, f"vendor LLM SDK {vendor!r} was imported"


# --- extract() basics ------------------------------------------------------


async def test_extract_returns_parsed_data() -> None:
    llm, _ = make_llm(json.dumps(BLOB))
    assert await extract(make_result(), schema=SCHEMA, llm=llm) == BLOB


async def test_extract_from_raw_string_source() -> None:
    llm, _ = make_llm(json.dumps(BLOB))
    assert await extract("# Widget costs $9.99", schema=SCHEMA, llm=llm) == BLOB


async def test_extract_falls_back_to_text_when_markdown_empty() -> None:
    llm, prompts = make_llm(json.dumps(BLOB))
    result = ScrapeResult(
        url="u", text="plain text body", markdown="  ", html=None, engine="stub", elapsed_ms=1
    )
    await extract(result, schema=SCHEMA, llm=llm)
    assert "plain text body" in prompts[0]


async def test_empty_source_raises_before_llm_call() -> None:
    llm, prompts = make_llm(json.dumps(BLOB))
    result = ScrapeResult(url="u", text=" ", markdown=" ", html=None, engine="stub", elapsed_ms=1)
    with pytest.raises(ValueError, match="no content"):
        await extract(result, schema=SCHEMA, llm=llm)
    assert prompts == []


# --- prompt construction ---------------------------------------------------


async def test_prompt_contains_schema_content_and_instructions() -> None:
    llm, prompts = make_llm(json.dumps(BLOB))
    await extract(
        make_result("# Widget page"),
        schema=SCHEMA,
        llm=llm,
        instructions="Prices in USD only.",
    )
    prompt = prompts[0]
    assert '"title"' in prompt  # schema serialized in
    assert "# Widget page" in prompt  # page content in
    assert "Prices in USD only." in prompt  # extra guidance in
    assert "ONLY the JSON" in prompt  # output contract stated


async def test_schema_as_natural_language_string() -> None:
    llm, prompts = make_llm(json.dumps(BLOB))
    data = await extract(
        make_result(),
        schema="the product title and its numeric price",
        llm=llm,
    )
    assert data == BLOB
    assert "the product title and its numeric price" in prompts[0]


async def test_content_truncated_to_max_content_chars() -> None:
    llm, prompts = make_llm(json.dumps(BLOB))
    await extract("x" * 500, schema=SCHEMA, llm=llm, max_content_chars=100)
    assert "x" * 100 + _TRUNCATION_MARKER in prompts[0]
    assert "x" * 101 not in prompts[0]


async def test_no_truncation_when_max_content_chars_none() -> None:
    big = "y" * (DEFAULT_MAX_CONTENT_CHARS + 10)
    llm, prompts = make_llm(json.dumps(BLOB))
    await extract(big, schema=SCHEMA, llm=llm, max_content_chars=None)
    assert big in prompts[0]


# --- lenient reply parsing -------------------------------------------------


async def test_code_fenced_reply_is_parsed() -> None:
    llm, _ = make_llm(f"```json\n{json.dumps(BLOB)}\n```")
    assert await extract(make_result(), schema=SCHEMA, llm=llm) == BLOB


async def test_reply_with_prose_around_json_is_parsed() -> None:
    llm, _ = make_llm(f"Sure! Here is the data:\n{json.dumps(BLOB)}\nHope that helps.")
    assert await extract(make_result(), schema=SCHEMA, llm=llm) == BLOB


async def test_top_level_array_reply() -> None:
    items = [{"title": "A"}, {"title": "B"}]
    llm, _ = make_llm(json.dumps(items))
    assert await extract(make_result(), schema={"type": "array"}, llm=llm) == items


# --- retry loop ------------------------------------------------------------


async def test_retries_on_invalid_json_then_succeeds() -> None:
    llm, prompts = make_llm("this is not json at all", json.dumps(BLOB))
    data = await extract(make_result(), schema=SCHEMA, llm=llm, max_retries=1)
    assert data == BLOB
    assert len(prompts) == 2
    # The corrective prompt feeds the failure and the bad reply back.
    assert "previous reply was rejected" in prompts[1]
    assert "this is not json at all" in prompts[1]


async def test_extraction_error_after_retries_exhausted() -> None:
    llm, prompts = make_llm("still not json")
    with pytest.raises(ExtractionError) as excinfo:
        await extract(make_result(), schema=SCHEMA, llm=llm, max_retries=2)
    assert len(prompts) == 3  # 1 + 2 retries
    assert excinfo.value.raw_reply == "still not json"
    assert "3 attempt(s)" in str(excinfo.value)


async def test_scalar_reply_rejected() -> None:
    llm, _ = make_llm("42")
    with pytest.raises(ExtractionError, match="expected object or array"):
        await extract(make_result(), schema="whatever", llm=llm, max_retries=0)


# --- structural schema check ----------------------------------------------


async def test_object_schema_rejects_array_reply() -> None:
    llm, _ = make_llm(json.dumps([1, 2, 3]))
    with pytest.raises(ExtractionError, match="type=object"):
        await extract(make_result(), schema=SCHEMA, llm=llm, max_retries=0)


async def test_array_schema_rejects_object_reply() -> None:
    llm, _ = make_llm(json.dumps(BLOB))
    with pytest.raises(ExtractionError, match="type=array"):
        await extract(make_result(), schema={"type": "array"}, llm=llm, max_retries=0)


async def test_missing_required_keys_rejected_then_corrected() -> None:
    llm, prompts = make_llm(json.dumps({"title": "Widget"}), json.dumps(BLOB))
    data = await extract(make_result(), schema=SCHEMA, llm=llm, max_retries=1)
    assert data == BLOB
    assert "missing required keys" in prompts[1]
    assert "price" in prompts[1]


async def test_natural_language_schema_skips_structural_check() -> None:
    # A list reply is fine when the schema is free-form text.
    llm, _ = make_llm(json.dumps([{"a": 1}]))
    assert await extract(make_result(), schema="a list of things", llm=llm) == [{"a": 1}]
