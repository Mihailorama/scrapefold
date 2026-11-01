"""Obscura engine — lightweight Rust headless browser driven over CDP.

Obscura (https://github.com/h4ckf0r0day/obscura, Apache-2.0) runs V8 with its
own DOM, presents a Chrome User-Agent and Chrome-consistent fingerprint, and
starts instantly. In the 2026-11-01 check (v0.2.6) it returned full Amazon
product and LinkedIn company pages where Lightpanda got a captcha or an auth
wall. Its ``--stealth`` mode (TLS impersonation) did *worse* on the same set
(Amazon captcha, Vercel challenge), so it is opt-in only.

Per fetch the engine starts ``obscura serve --port 0`` (≈0.2 s), connects with
Playwright ``connect_over_cdp`` and loads one page. CDP is used instead of
``obscura fetch`` because the CLI never reports the HTTP status, headers or
cookies — without them the router can neither flag a 403 nor reclassify a
DataDome / Cloudflare block (SCRAPEFOLD-5).

Install: download a release archive and put ``obscura`` on PATH (override with
``extra["obscura_binary"]`` or ``$OBSCURA_BIN``), plus
``pip install scrapefold[obscura]`` for the Playwright client (no browser
download needed).

Native parameter surface:

| Native                              | Unified option                    | Default |
|-------------------------------------|-----------------------------------|---------|
| ``page.goto(wait_until=)``          | ``wait_until``                    | ``domcontentloaded`` |
| ``page.wait_for_selector``          | ``wait_for_selector``             | none    |
| ``serve --proxy``                   | ``proxy``                         | none    |
| ``serve --user-agent``              | ``user_agent``                    | Chrome UA |
| ``set_extra_http_headers``          | ``language`` + ``custom_headers`` | none    |
| ``context.add_cookies``             | ``cookies``                       | none    |
| goto timeout (capped, see below)    | ``timeout_s``                     | 25 s cap |
| ``serve --stealth``                 | ``extra["obscura_stealth"]``      | off     |
| ``serve --obey-robots``             | ``extra["obscura_obey_robots"]``  | off     |

The per-page timeout is ``min(timeout_s, extra["obscura_timeout_s"]=25)``: one
slow page (Zillow took 41 s) must not eat the whole 60 s walk budget. On a
navigation timeout the DOM loaded so far is still returned and detection
decides whether it is usable.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from typing import Any
from urllib.parse import urlparse

from scrapefold.engines._cli import check_redirect_scope, resolve_binary
from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_CAP_S = 25
_READY_WAIT_S = 10


def _serve_args(opts: ScrapeOptions, ready_file: str) -> list[str]:
    """``obscura serve`` flags (binary excluded)."""
    args = ["serve", "--port", "0", "--quiet", "--ready-file", ready_file]
    if opts.proxy:
        args += ["--proxy", opts.proxy]
    if opts.user_agent:
        args += ["--user-agent", opts.user_agent]
    if opts.extra.get("obscura_stealth"):
        args.append("--stealth")
    if opts.extra.get("obscura_obey_robots"):
        args.append("--obey-robots")
    return args


def _timeout_s(opts: ScrapeOptions) -> float:
    return float(min(opts.timeout_s, opts.extra.get("obscura_timeout_s", _DEFAULT_TIMEOUT_CAP_S)))


def _load_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright  # lazy
    except ImportError as exc:
        raise ImportError(
            "playwright is required for ObscuraEngine. "
            "Install it with: pip install 'scrapefold[obscura]'"
        ) from exc
    return async_playwright


async def _wait_ready(proc: asyncio.subprocess.Process, ready_file: str) -> str:
    """Return the CDP endpoint once ``obscura serve`` has written its ready file."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _READY_WAIT_S
    while loop.time() < deadline:
        if proc.returncode is not None:
            raise RuntimeError(f"obscura serve exited with {proc.returncode}")
        with contextlib.suppress(FileNotFoundError, ValueError):
            info = json.loads(open(ready_file).read())  # noqa: SIM115 — tiny file
            return f"http://{info['host']}:{info['port']}"
        await asyncio.sleep(0.05)
    raise TimeoutError("obscura serve did not become ready")


async def _page_html(page: Any) -> str:
    """``page.content()``, retried once: after a timeout the page can still be
    mid-navigation (client-side redirect) and Playwright refuses to read it."""
    try:
        return str(await page.content())
    except Exception as exc:
        if "navigating" not in str(exc):
            raise
        await asyncio.sleep(1.0)
        return str(await page.content())


class ObscuraEngine(ScrapeEngine):
    """Local Obscura browser over CDP — free JS rendering with a Chrome-like fingerprint."""

    NAME = "obscura"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="datacenter",
        output_native_markdown=False,
        default_timeout_s=_DEFAULT_TIMEOUT_CAP_S,
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "render_js",
            "wait_until",
            "wait_for_selector",
            "proxy",
            "user_agent",
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
        return resolve_binary(extra.get("obscura_binary"), "OBSCURA_BIN", "obscura")

    def is_available(self) -> bool:
        return self._binary() is not None

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        async_playwright = _load_playwright()
        binary = self._binary(opts)
        if binary is None:
            raise RuntimeError("obscura binary not found on PATH (set $OBSCURA_BIN)")

        with tempfile.TemporaryDirectory(prefix="obscura-") as tmp:
            ready_file = os.path.join(tmp, "ready.json")
            proc = await asyncio.create_subprocess_exec(
                binary,
                *_serve_args(opts, ready_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                endpoint = await _wait_ready(proc, ready_file)
                async with async_playwright() as pw:
                    browser = await pw.chromium.connect_over_cdp(endpoint)
                    try:
                        return await self._load_page(browser, url, opts)
                    finally:
                        await browser.close()
            finally:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()

    async def _load_page(self, browser: Any, url: str, opts: ScrapeOptions) -> ScrapeResult:
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        headers = build_target_headers(opts, include_cookies=False, include_user_agent=False)
        if headers:
            await context.set_extra_http_headers(headers)
        if opts.cookies:
            host = urlparse(url).hostname or ""
            await context.add_cookies(
                [
                    {"name": k, "value": v, "domain": host, "path": "/"}
                    for k, v in opts.cookies.items()
                ]
            )
        page = await context.new_page()
        timeout_ms = _timeout_s(opts) * 1000
        response = None
        timed_out = False
        try:
            response = await page.goto(url, wait_until=opts.wait_until, timeout=timeout_ms)
            if opts.wait_for_selector:
                await page.wait_for_selector(opts.wait_for_selector, timeout=timeout_ms)
        except Exception as exc:
            # A navigation/selector timeout still leaves a usable DOM most of the
            # time; any other failure (DNS, refused) is a real engine error.
            if "Timeout" not in type(exc).__name__:
                raise
            timed_out = True
            logger.debug(
                "obscura: timeout after %.0f ms for %s; using partial DOM", timeout_ms, url
            )

        final_url = page.url or url
        check_redirect_scope(self.NAME, final_url, opts.extra)
        html = await _page_html(page)
        resp_headers = await response.all_headers() if response is not None else {}
        cookies = {c["name"]: c["value"] for c in await context.cookies()}
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
                "status_code": response.status if response is not None else None,
                "final_url": final_url,
                "headers": resp_headers,
                "cookies": cookies,
                "timed_out": timed_out,
            },
        )


__all__ = ["ObscuraEngine", "_serve_args", "_timeout_s"]
