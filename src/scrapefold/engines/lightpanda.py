"""Lightpanda engine — fast, low-memory JS rendering via the ``lightpanda`` binary.

Lightpanda (https://github.com/lightpanda-io/browser, AGPL-3.0) is a headless
browser written in Zig with V8 but no rendering engine. It is ~10x faster than
a Chromium-based engine on friendly JS pages, but has **no stealth**: it
identifies itself as ``Lightpanda/X.Y`` and refuses any User-Agent containing
"Mozilla". Use it as the cheap JS step before stealth browsers, never against
anti-bot targets.

Install: download the release binary and put it on PATH (it is not bundled —
scrapefold only spawns it as a separate process, which keeps AGPL out of the
package). Override the path with ``extra["lightpanda_binary"]`` or
``$LIGHTPANDA_BIN``. Telemetry is always disabled.

Native parameter surface (``lightpanda fetch``):

| Native flag          | Unified option                      | Default      |
|----------------------|-------------------------------------|--------------|
| ``--wait-until``     | ``wait_until``                      | ``domcontentloaded`` |
| ``--wait-selector``  | ``wait_for_selector``               | none         |
| ``--http-proxy``     | ``proxy``                           | none         |
| ``--locale``         | ``language``                        | ``en-US``    |
| ``--http-header``    | ``custom_headers`` + ``cookies``    | none         |
| ``--http-timeout``   | ``timeout_s`` (ms)                  | 15000        |
| ``--obey-robots``    | ``extra["lightpanda_obey_robots"]`` | off          |
"""

from __future__ import annotations

import json
import logging

from scrapefold.engines._cli import check_redirect_scope, resolve_binary, run_cli
from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_ENV = {"LIGHTPANDA_DISABLE_TELEMETRY": "true", "LIGHTPANDA_DISABLE_CORE_DUMP": "1"}


def _adapt(opts: ScrapeOptions) -> list[str]:
    """Map unified options to ``lightpanda fetch`` flags (URL excluded)."""
    args = ["fetch", "--json", "--dump", "html", "--log-level", "fatal"]
    args += ["--wait-until", opts.wait_until]
    if opts.wait_for_selector:
        args += ["--wait-selector", opts.wait_for_selector]
    if opts.proxy:
        args += ["--http-proxy", opts.proxy]
    if opts.language:
        args += ["--locale", opts.language.split(",")[0].strip()]
    # Accept-Language is driven by --locale; UA is forbidden to spoof.
    headers = build_target_headers(opts, include_user_agent=False)
    headers.pop("Accept-Language", None)
    for name, value in headers.items():
        args += ["--http-header", f"{name}: {value}"]
    args += ["--http-timeout", str(int(opts.timeout_s * 1000))]
    if opts.extra.get("lightpanda_obey_robots"):
        args.append("--obey-robots")
    return args


class LightpandaEngine(ScrapeEngine):
    """Local Lightpanda binary — free, fast JS rendering without stealth."""

    NAME = "lightpanda"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="datacenter",
        output_native_markdown=False,
        default_timeout_s=30,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "render_js",
            "wait_until",
            "wait_for_selector",
            "proxy",
            "language",
            "custom_headers",
            "cookies",
            "output_format",
            "timeout_s",
            "extra",
        }
    )

    def _binary(self, opts: ScrapeOptions | None = None) -> str | None:
        extra = opts.extra if opts else {}
        return resolve_binary(extra.get("lightpanda_binary"), "LIGHTPANDA_BIN", "lightpanda")

    def is_available(self) -> bool:
        return self._binary() is not None

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        binary = self._binary(opts)
        if binary is None:
            raise RuntimeError("lightpanda binary not found on PATH (set $LIGHTPANDA_BIN)")
        argv = [binary, *_adapt(opts), url]
        logger.debug("lightpanda fetch url=%s", url)
        # Outer deadline > --http-timeout so the binary reports its own error first.
        raw = await run_cli(argv, timeout_s=opts.timeout_s + 10, env=_ENV)
        data = json.loads(raw)
        if data.get("error"):
            raise RuntimeError(f"navigation failed: {data['error']}")
        final_url = data.get("url") or url
        check_redirect_scope(self.NAME, final_url, opts.extra)

        headers = {h["name"].lower(): h["value"] for h in data.get("headers") or []}
        cookies = {
            h["value"].split("=", 1)[0].strip(): ""
            for h in data.get("headers") or []
            if h["name"].lower() == "set-cookie" and "=" in h["value"]
        }
        html = data.get("content") or ""
        text, markdown = html_to_both(html, base_url=final_url)
        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=0.0,
            meta={
                "status_code": data.get("http_status"),
                "final_url": final_url,
                "bot_challenge": data.get("bot_challenge"),
                "headers": headers,
                "cookies": cookies,
            },
        )


__all__ = ["LightpandaEngine", "_adapt"]
