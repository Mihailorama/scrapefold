"""Mask API keys that keyed engines pass as query parameters.

httpx logs every request at INFO as ``HTTP Request: GET <full url> ...``; for
Scrapingdog and other vendors that take the key as ``?api_key=...`` that line
would carry the secret. A filter on the ``httpx`` logger rewrites the rendered
message with the value masked.
"""

from __future__ import annotations

import logging
import re

_SECRET_PARAM = re.compile(
    r"(?i)([?&](?:api_key|apikey|api-key|access_key|access_token|token|key)=)[^&\s\"']+"
)


class _MaskSecretParams(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = _SECRET_PARAM.sub(r"\1***", message)
        if masked != message:
            record.msg, record.args = masked, None
        return True


def install() -> None:
    """Attach the filter to the httpx logger once (idempotent)."""
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _MaskSecretParams) for f in httpx_logger.filters):
        httpx_logger.addFilter(_MaskSecretParams())
