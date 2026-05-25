"""Tests for scrapefold.detection — is_suspicious() and reclassify_from_response()."""

from __future__ import annotations

from scrapefold.detection import is_suspicious, reclassify_from_response
from scrapefold.ladders import Signature
from scrapefold.result import ScrapeResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(
    *,
    text: str = "",
    html: str | None = None,
    meta: dict | None = None,
) -> ScrapeResult:
    return ScrapeResult(
        url="https://example.com",
        text=text,
        markdown=text,
        html=html,
        engine="test",
        elapsed_ms=100,
        meta=meta or {},
    )


# ---------------------------------------------------------------------------
# is_suspicious — normal / clean results
# ---------------------------------------------------------------------------


class TestIsSuspiciousClean:
    def test_rich_text_is_not_suspicious(self) -> None:
        result = _result(text="A" * 300)
        assert is_suspicious(result) is False

    def test_custom_min_text_chars_lower_threshold(self) -> None:
        # 100 chars is below default 200 but above custom 50
        result = _result(text="A" * 100)
        assert is_suspicious(result, min_text_chars=50) is False

    def test_result_with_200_exact_chars_is_not_suspicious(self) -> None:
        result = _result(text="B" * 200)
        assert is_suspicious(result) is False


# ---------------------------------------------------------------------------
# is_suspicious — text length heuristic
# ---------------------------------------------------------------------------


class TestIsSuspiciousTextLength:
    def test_empty_text_is_suspicious(self) -> None:
        # Empty text (whitespace-stripped) is always suspicious regardless of status.
        result = _result(text="")
        assert is_suspicious(result) is True

    def test_whitespace_only_text_is_suspicious(self) -> None:
        # Whitespace-only is treated as empty — suspicious regardless of status.
        result = _result(text="   \n\t  ")
        assert is_suspicious(result) is True

    def test_short_text_with_error_status_is_suspicious(self) -> None:
        # Short text + 4xx → suspicious (conjoint condition met via error status).
        result = _result(text="hi", meta={"status_code": 403})
        assert is_suspicious(result) is True

    def test_short_text_with_5xx_status_is_suspicious(self) -> None:
        # Short text + 5xx → suspicious.
        result = _result(text="A" * 199, meta={"status_code": 503})
        assert is_suspicious(result) is True

    def test_short_text_with_200_is_not_suspicious_by_rule1(self) -> None:
        # Short but non-empty text + 200 → NOT suspicious under Rule 1.
        # (Other rules can still flag it, but this tests Rule 1 isolation.)
        result = _result(text="hi", meta={"status_code": 200})
        assert is_suspicious(result) is False

    def test_short_text_no_status_is_not_suspicious_by_rule1(self) -> None:
        # Short non-empty text with no status code → NOT suspicious under Rule 1.
        result = _result(text="A" * 199)
        assert is_suspicious(result) is False


# ---------------------------------------------------------------------------
# is_suspicious — antibot phrase heuristic
# ---------------------------------------------------------------------------


class TestIsSuspiciousAntibotPhrases:
    def test_default_phrase_in_text_is_suspicious(self) -> None:
        long_text = "A" * 300 + "Just a moment..." + "B" * 300
        result = _result(text=long_text)
        assert is_suspicious(result) is True

    def test_default_phrase_case_insensitive_in_html(self) -> None:
        # phrase is in html only, text is rich enough
        result = _result(
            text="A" * 300,
            html="<html><body>JUST A MOMENT...</body></html>",
        )
        assert is_suspicious(result) is True

    def test_phrase_in_html_not_text(self) -> None:
        result = _result(
            text="A" * 300,
            html="<html>cf-browser-verification</html>",
        )
        assert is_suspicious(result) is True

    def test_custom_phrase_triggers_suspicion(self) -> None:
        result = _result(text="A" * 300 + "blocked by firewall")
        assert is_suspicious(result, antibot_phrases=("blocked by firewall",)) is True

    def test_no_phrase_match_with_rich_text(self) -> None:
        result = _result(text="A" * 300, html="<html><body>" + "A" * 300 + "</body></html>")
        assert is_suspicious(result) is False


# ---------------------------------------------------------------------------
# is_suspicious — noscript domination heuristic
# ---------------------------------------------------------------------------


class TestIsSuspiciousNoscript:
    def test_html_dominated_by_noscript_is_suspicious(self) -> None:
        noscript_content = "x" * 900
        tiny_real = "y" * 10
        html = f"<html><noscript>{noscript_content}</noscript>{tiny_real}</html>"
        result = _result(text="A" * 300, html=html)
        assert is_suspicious(result) is True

    def test_html_with_minor_noscript_is_not_suspicious(self) -> None:
        html = "<html><body>" + "A" * 400 + "</body><noscript>small</noscript></html>"
        result = _result(text="A" * 300, html=html)
        assert is_suspicious(result) is False


# ---------------------------------------------------------------------------
# is_suspicious — script-dominated HTML heuristic
# ---------------------------------------------------------------------------


class TestIsSuspiciousScriptDominated:
    def test_html_mostly_script_tags_is_suspicious(self) -> None:
        script_block = "<script>" + "x" * 900 + "</script>"
        tiny_text = "tiny"
        html = f"<html><body>{tiny_text}{script_block}</body></html>"
        result = _result(text="A" * 300, html=html)
        assert is_suspicious(result) is True

    def test_html_with_substantial_text_not_suspicious(self) -> None:
        script_block = "<script>" + "x" * 50 + "</script>"
        text_block = "content " * 100  # lots of text
        html = f"<html><body>{text_block}{script_block}</body></html>"
        result = _result(text="A" * 300, html=html)
        assert is_suspicious(result) is False


# ---------------------------------------------------------------------------
# is_suspicious — status code heuristic via meta
# ---------------------------------------------------------------------------


class TestIsSuspiciousStatusCode:
    def test_403_with_empty_text_is_suspicious(self) -> None:
        result = _result(text="", meta={"status_code": 403})
        assert is_suspicious(result) is True

    def test_503_with_empty_text_is_suspicious(self) -> None:
        result = _result(text="", meta={"status_code": 503})
        assert is_suspicious(result) is True

    def test_403_with_rich_text_is_suspicious_via_block_status(self) -> None:
        # Block-status codes (403, 429, 503) always flag suspicious regardless
        # of body length — the body is a synthetic error page, never real
        # content. Found via live smoke: a Cloudflare-protected target
        # returned a ~500-byte localised "access denied" page on 403 that
        # was previously silently accepted as success.
        result = _result(text="A" * 300, meta={"status_code": 403})
        assert is_suspicious(result) is True

    def test_429_rate_limit_is_suspicious(self) -> None:
        result = _result(text="Too Many Requests" + "A" * 300, meta={"status_code": 429})
        assert is_suspicious(result) is True

    def test_404_with_rich_text_is_not_suspicious(self) -> None:
        # 404 is a legitimate protocol response — escalation will not help, so
        # the router must NOT flag it suspicious.
        result = _result(text="Page not found" + "A" * 300, meta={"status_code": 404})
        assert is_suspicious(result) is False

    def test_200_with_empty_text_still_suspicious_via_length(self) -> None:
        result = _result(text="", meta={"status_code": 200})
        assert is_suspicious(result) is True


# ---------------------------------------------------------------------------
# reclassify_from_response
# ---------------------------------------------------------------------------


class TestReclassifyFromResponse:
    def test_cloudflare_body_phrase_and_cookie(self) -> None:
        result = reclassify_from_response(
            body="Just a moment...",
            cookies={"__cf_bm": "abc123"},
        )
        assert result == "cloudflare_protected"

    def test_cloudflare_header_and_body_phrase(self) -> None:
        result = reclassify_from_response(
            body="Checking your browser",
            headers={"cf-ray": "xyz"},
        )
        assert result == "cloudflare_protected"

    def test_datadome_cookie_and_body_phrase(self) -> None:
        result = reclassify_from_response(
            body="redirect to /captcha-delivery/",
            cookies={"datadome": "token"},
        )
        assert result == "datadome_protected"

    def test_perimeterx_cookies_match(self) -> None:
        result = reclassify_from_response(
            cookies={"_px": "a", "_px2": "b"},
        )
        assert result == "perimeterx_protected"

    def test_akamai_two_cookies_match(self) -> None:
        result = reclassify_from_response(
            cookies={"_abck": "val", "bm_sz": "val2"},
        )
        assert result == "akamai_protected"

    def test_no_match_returns_none(self) -> None:
        result = reclassify_from_response(
            body="Hello world",
            cookies={"session": "abc"},
            headers={"content-type": "text/html"},
            status_code=200,
        )
        assert result is None

    def test_single_match_below_threshold_returns_none(self) -> None:
        # Only one CF cookie — min_matches=2, so should not trigger
        result = reclassify_from_response(
            cookies={"__cf_bm": "x"},
        )
        assert result is None

    def test_first_matching_signature_wins(self) -> None:
        # Datadome should win over Cloudflare when both cookies present
        # because SIGNATURES lists datadome before cloudflare.
        result = reclassify_from_response(
            body="/captcha-delivery/ and geo.captcha-delivery.com",
            cookies={"datadome": "x", "__cf_bm": "y"},
        )
        assert result == "datadome_protected"

    def test_custom_signatures_tuple_respected(self) -> None:
        custom = (
            Signature(
                target="static_general",
                body_phrases_any=("custom-block",),
                status_codes=(999,),
                min_matches=2,
            ),
        )
        result = reclassify_from_response(
            body="custom-block",
            status_code=999,
            signatures=custom,
        )
        assert result == "static_general"

    def test_header_names_are_case_insensitive(self) -> None:
        # CF header is "cf-ray"; pass it uppercased
        result = reclassify_from_response(
            body="Checking your browser",
            headers={"CF-RAY": "xyz"},
        )
        assert result == "cloudflare_protected"

    def test_all_none_inputs_returns_none(self) -> None:
        result = reclassify_from_response()
        assert result is None

    def test_body_phrases_all_requires_all_phrases(self) -> None:
        custom = (
            Signature(
                target="static_general",
                body_phrases_all=("alpha", "beta"),
                min_matches=1,
            ),
        )
        # Only one phrase present — should NOT match
        assert reclassify_from_response(body="alpha only", signatures=custom) is None
        # Both present — should match
        assert (
            reclassify_from_response(body="alpha and beta here", signatures=custom)
            == "static_general"
        )

    def test_status_codes_filter_applied(self) -> None:
        custom = (
            Signature(
                target="static_general",
                body_phrases_any=("blocked",),
                status_codes=(403,),
                min_matches=2,
            ),
        )
        # status_code must match; body phrase alone isn't enough
        assert reclassify_from_response(body="blocked", status_code=200, signatures=custom) is None
        assert (
            reclassify_from_response(body="blocked", status_code=403, signatures=custom)
            == "static_general"
        )
