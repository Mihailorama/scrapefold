"""Response-quality detection for the scrapefold router.

Two public functions:

* :func:`is_suspicious` — decides whether a :class:`~scrapefold.result.ScrapeResult`
  looks blocked / empty and should trigger ladder escalation.
* :func:`reclassify_from_response` — matches response signals against
  :data:`~scrapefold.ladders.SIGNATURES` to determine whether the site should
  be reclassified into a vendor anti-bot :data:`~scrapefold.ladders.SiteClass`.

Both are pure sync functions; no I/O, no network calls.
"""

from __future__ import annotations

import logging
import re

from scrapefold.ladders import SIGNATURES, Signature, SiteClass
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default antibot phrases
# ---------------------------------------------------------------------------

DEFAULT_ANTIBOT_PHRASES: tuple[str, ...] = (
    "Just a moment...",
    "Verify you are human",
    "Checking your browser",
    "Access denied",
    "Please enable JavaScript",
    "cf-browser-verification",
)

# Pre-compiled patterns used by the HTML heuristics.
_RE_NOSCRIPT = re.compile(r"<noscript[^>]*>.*?</noscript>", re.IGNORECASE | re.DOTALL)
_RE_SCRIPT = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_suspicious(
    result: ScrapeResult,
    *,
    min_text_chars: int = 200,
    antibot_phrases: tuple[str, ...] = DEFAULT_ANTIBOT_PHRASES,
) -> bool:
    """Return True if the scrape result looks like an anti-bot block or empty page.

    Heuristics applied (any one is sufficient):

    1. **Short text** — ``len(result.text) < min_text_chars``.
    2. **Anti-bot phrase** — any phrase in *antibot_phrases* appears (case-
       insensitive) in ``result.text`` or ``result.html``.
    3. **Noscript domination** — the ``<noscript>`` content exceeds 50 % of the
       raw HTML length after stripping noscript tags.
    4. **Script domination** — the ratio of non-script visible text to total HTML
       is below 0.1 (script tags make up more than 90 % of the document).
    5. **Error status + empty text** — ``meta["status_code"]`` is 403 or 503 AND
       ``result.text`` is empty / whitespace-only.
    """
    text: str = result.text or ""
    html: str | None = result.html

    # Heuristic 1 — short text.
    if len(text) < min_text_chars:
        logger.debug("is_suspicious: short text (%d < %d chars)", len(text), min_text_chars)
        return True

    # Heuristic 2 — antibot phrases in text or HTML.
    text_lower = text.lower()
    html_lower = (html or "").lower()
    for phrase in antibot_phrases:
        phrase_lower = phrase.lower()
        if phrase_lower in text_lower or phrase_lower in html_lower:
            logger.debug("is_suspicious: antibot phrase %r found", phrase)
            return True

    if html:
        html_len = len(html)

        # Heuristic 3 — noscript domination.
        # Compare total noscript content length to full HTML length.
        noscript_matches = _RE_NOSCRIPT.findall(html)
        if noscript_matches:
            noscript_total = sum(len(m) for m in noscript_matches)
            stripped_len = html_len - noscript_total
            if html_len > 0 and stripped_len < html_len * 0.5:
                logger.debug(
                    "is_suspicious: noscript domination (noscript=%d, total=%d)",
                    noscript_total,
                    html_len,
                )
                return True

        # Heuristic 4 — script domination.
        # Strip all <script>…</script> blocks and compare remaining length.
        if html_len > 0:
            script_matches = _RE_SCRIPT.findall(html)
            script_total = sum(len(m) for m in script_matches)
            non_script_len = html_len - script_total
            ratio = non_script_len / html_len
            if ratio < 0.1:
                logger.debug(
                    "is_suspicious: script domination (ratio=%.3f, script=%d, total=%d)",
                    ratio,
                    script_total,
                    html_len,
                )
                return True

    # Heuristic 5 — HTTP error status with empty body.
    status_code: int | None = result.meta.get("status_code")
    if status_code in (403, 503) and not text.strip():
        logger.debug("is_suspicious: status_code=%d with empty text", status_code)
        return True

    return False


def reclassify_from_response(
    *,
    body: str | None = None,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    status_code: int | None = None,
    signatures: tuple[Signature, ...] = SIGNATURES,
) -> SiteClass | None:
    """Match the response against *signatures*; return the target SiteClass if
    any :class:`~scrapefold.ladders.Signature` meets its ``min_matches``
    threshold, else ``None``.

    Walk order matters — the first matching signature wins.  SIGNATURES is
    ordered so that narrow vendor signatures (Datadome, PerimeterX, Akamai)
    beat the broader Cloudflare one.

    Matching rules per signature field:

    * ``cookie_names`` — each cookie **name** present in *cookies* scores 1.
    * ``header_names`` — each header name (compared lower-case) present in
      *headers* scores 1.
    * ``body_phrases_all`` — if non-empty, **all** phrases must appear in
      *body* (case-insensitive); if they do, scores 1 collectively.
    * ``body_phrases_any`` — if non-empty, at least one phrase must appear in
      *body* (case-insensitive); scores 1.
    * ``status_codes`` — if non-empty, *status_code* must be in the set; if it
      is, scores 1.

    If ``status_codes`` is non-empty and *status_code* is NOT in it, the
    signature is skipped entirely (acts as a filter, not just a scorer).
    """
    body_lower = (body or "").lower()
    cookies_norm: dict[str, str] = cookies or {}
    # Normalise header names to lower-case once.
    headers_lower: dict[str, str] = {k.lower(): v for k, v in (headers or {}).items()}

    for sig in signatures:
        # status_codes acts as a hard filter when non-empty.
        if sig.status_codes and status_code not in sig.status_codes:
            continue

        score = 0

        # Score: status_code present in sig.status_codes (already passed filter).
        if sig.status_codes and status_code in sig.status_codes:
            score += 1

        # Score: cookie name matches.
        for name in sig.cookie_names:
            if name in cookies_norm:
                score += 1

        # Score: header name matches (lower-case comparison).
        for name in sig.header_names:
            if name.lower() in headers_lower:
                score += 1

        # Score: body_phrases_all — all phrases must match; counts as 1 if so.
        if sig.body_phrases_all and all(
            phrase.lower() in body_lower for phrase in sig.body_phrases_all
        ):
            score += 1

        # Score: body_phrases_any — at least one phrase matches; counts as 1.
        if sig.body_phrases_any and any(
            phrase.lower() in body_lower for phrase in sig.body_phrases_any
        ):
            score += 1

        if score >= sig.min_matches:
            logger.debug(
                "reclassify_from_response: matched signature %r (score=%d)",
                sig.target,
                score,
            )
            return sig.target

    return None


__all__ = [
    "DEFAULT_ANTIBOT_PHRASES",
    "is_suspicious",
    "reclassify_from_response",
]
