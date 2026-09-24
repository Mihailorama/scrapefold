"""CloakBrowser engine for scrapefold.

Local stealth browser via the cloakbrowser SDK (Playwright + fingerprint
hardening). Runs locally by default; optional 2Captcha solving incurs a charge.

SDK: cloakbrowser (``pip install 'cloakbrowser>=0.3'``).

Usage pattern:
    ctx = await cloakbrowser.launch_context_async(
        headless=True,
        stealth_args=True,    # fingerprint hardening, always on
        user_agent=...,       # optional UA override
        locale=...,           # from opts.language
        proxy=...,            # ProxySettings or URL string
        extra_http_headers={...},  # forwarded to browser.new_context()
    )
    page = await ctx.new_page()
    await page.goto(url, wait_until="load", timeout=<ms>)
    html = await page.content()
    screenshot_bytes = await page.screenshot(type="png")   # optional
    await ctx.close()

Contract pinned against cloakbrowser 0.3.28.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both
from scrapefold.options import ScrapeOptions, build_target_headers, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

_DEFAULT_OPTS = ScrapeOptions()

# Module-level references populated lazily on first use.
# Exposed at module scope so tests can monkeypatch them:
#   patch("scrapefold.engines.cloakbrowser.launch_context_async", ...)
launch_context_async: Any = None
ProxySettings: Any = None


async def _solve_aws_waf(page: Any, url: str, key: str, timeout_s: int) -> float:
    """Use 2Captcha for a rendered AWS WAF challenge or CAPTCHA, then reload."""
    params = await page.evaluate("""() => {
        const scripts = Array.from(document.scripts, script => script.src);
        const find = name => scripts.find(src => {
            try {
                const parsed = new URL(src);
                return parsed.hostname.endsWith('.awswaf.com') &&
                    parsed.pathname.endsWith('/' + name);
            } catch { return false; }
        });
        return {
            websiteKey: window.gokuProps?.key,
            iv: window.gokuProps?.iv,
            context: window.gokuProps?.context,
            challengeScript: find('challenge.js'),
            captchaScript: find('captcha.js'),
            jsapiScript: find('jsapi.js'),
        };
    }""")
    if (
        not isinstance(params, dict)
        or not isinstance(params.get("websiteKey"), str)
        or not params["websiteKey"]
    ):
        return 0.0
    task: dict[str, str] = {
        "type": "AmazonTaskProxyless",
        "websiteURL": url,
        "websiteKey": params["websiteKey"],
    }
    if isinstance(params.get("jsapiScript"), str) and params["jsapiScript"]:
        task["jsapiScript"] = params["jsapiScript"]
    elif all(isinstance(params.get(name), str) and params[name] for name in ("iv", "context")):
        task.update({name: params[name] for name in ("iv", "context")})
        for name in ("challengeScript", "captchaScript"):
            if isinstance(params.get(name), str) and params[name]:
                task[name] = params[name]
        if "challengeScript" not in task and "captchaScript" not in task:
            return 0.0
    else:
        return 0.0

    deadline = asyncio.get_running_loop().time() + timeout_s
    async with httpx.AsyncClient(timeout=float(min(timeout_s, 15))) as client:
        created = (
            await client.post(
                "https://api.2captcha.com/createTask", json={"clientKey": key, "task": task}
            )
        ).json()
        if created.get("errorId") or not isinstance(created.get("taskId"), int):
            logger.warning(
                "2captcha createTask failed: %s", created.get("errorCode", "invalid response")
            )
            return 0.0
        while asyncio.get_running_loop().time() + 5 < deadline:
            await asyncio.sleep(5)
            answer = (
                await client.post(
                    "https://api.2captcha.com/getTaskResult",
                    json={"clientKey": key, "taskId": created["taskId"]},
                )
            ).json()
            if answer.get("errorId"):
                logger.warning("2captcha getTaskResult failed: %s", answer.get("errorCode"))
                return 0.0
            if answer.get("status") == "ready":
                cost = float(answer.get("cost") or 0.0)
                solution = answer.get("solution") or {}
                voucher = solution.get("captcha_voucher") if isinstance(solution, dict) else None
                if not isinstance(voucher, str) or not voucher:
                    return cost
                token = solution.get("existing_token")
                try:
                    if isinstance(token, str) and token:
                        await page.context.add_cookies(
                            [{"name": "aws-waf-token", "value": token, "url": url}]
                        )
                    await page.evaluate(
                        "voucher => window.ChallengeScript.submitCaptcha(voucher)", voucher
                    )
                    await page.reload(wait_until="load", timeout=timeout_s * 1000)
                except Exception:
                    logger.warning("2captcha AWS WAF voucher could not be applied")
                return cost
            if answer.get("status") != "processing":
                return 0.0
    logger.warning("2captcha AWS WAF solve timed out")
    return 0.0


def _load_sdk() -> Any:
    """Return cloakbrowser.launch_context_async, importing on first call.

    Also caches ``ProxySettings`` at module scope so ``_build_proxy`` can
    reach it through one path with consistent install-hint error handling.

    Raises ImportError with an installation hint if cloakbrowser is missing.
    """
    global launch_context_async, ProxySettings
    if launch_context_async is not None:
        return launch_context_async
    try:
        import cloakbrowser as _cb  # lazy
    except ImportError as exc:
        raise ImportError(
            "cloakbrowser is required for CloakBrowserEngine. "
            "Install it with: pip install 'cloakbrowser>=0.3'"
        ) from exc
    launch_context_async = _cb.launch_context_async
    ProxySettings = _cb.ProxySettings
    return launch_context_async


def _build_proxy(opts: ScrapeOptions) -> Any | None:
    """Return a cloakbrowser-friendly proxy value, or ``None`` when the engine
    cannot honor the request.

    cloakbrowser runs locally and has no built-in residential pool, so
    ``premium_proxy=True`` by itself is unenforceable. Callers that want a
    real proxy must supply a concrete URL via ``extra["cloakbrowser_proxy"]``;
    anything else is dropped with a debug log (per the drop-not-raise rule).
    """
    if opts.extra:
        explicit = opts.extra.get("cloakbrowser_proxy")
        if explicit:
            return explicit

    if opts.premium_proxy:
        logger.debug(
            "cloakbrowser: premium_proxy=True without extra['cloakbrowser_proxy'] — "
            "dropped (no built-in residential pool)"
        )
    return None


def _adapt(opts: ScrapeOptions) -> dict[str, Any]:
    """Map ScrapeOptions to cloakbrowser.launch_context_async kwargs."""
    kwargs: dict[str, Any] = {
        "headless": True,
        "stealth_args": True,  # always on — this is a stealth browser
        "humanize": False,  # deterministic default; callers can override via extra
    }

    if opts.user_agent:
        kwargs["user_agent"] = opts.user_agent

    if opts.language:
        kwargs["locale"] = opts.language

    proxy = _build_proxy(opts)
    if proxy is not None:
        kwargs["proxy"] = proxy

    # user_agent has a dedicated kwarg above — keep it out of the headers dict
    # so the SDK manages the UA natively. Cookies stay in (Playwright forwards
    # them via extra_http_headers, since launch_context_async has no cookies kwarg).
    headers = build_target_headers(opts, include_user_agent=False)
    if headers:
        kwargs["extra_http_headers"] = headers

    # timeout_s drives page.goto timeout (SDK uses ms)
    # We keep it here so _fetch can read it separately from kwargs
    # (launch_context_async does not have a timeout param)

    # cloakbrowser_* extra keys forwarded as top-level kwargs
    extra_overrides = strip_extra_prefix(opts.extra, "cloakbrowser_")
    # Don't double-set proxy via the prefix strip — we handled it above
    extra_overrides.pop("proxy", None)
    kwargs.update(extra_overrides)

    return kwargs


class CloakBrowserEngine(ScrapeEngine):
    """Stealth browser engine powered by the cloakbrowser SDK.

    Runs a local Chromium instance with fingerprint-hardening args. No API
    key is required; cost per call is 0 unless 2Captcha is enabled.

    Ideal for Cloudflare-protected pages and sites that detect headless
    browsers via standard CDP/navigator fingerprints.

    The SDK (cloakbrowser) is lazy-imported so that importing scrapefold
    doesn't require it to be installed.
    """

    NAME = "cloakbrowser"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        estimated_cost_usd=0.0,
        billing_unit="call",
        requires_api_key=False,
        proxy_type="residential",
        output_native_markdown=False,
        default_timeout_s=90,
        avg_response_mb_estimate=15.0,  # full browser session
    )
    SUPPORTED_OPTIONS = frozenset(
        {
            "language",
            "country",
            "render_js",
            "wait_ms",
            "wait_for_selector",
            "stealth",
            "premium_proxy",
            "user_agent",
            "custom_headers",
            "cookies",
            "output_format",
            "take_screenshot",
            "timeout_s",
            "extra",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        # api_key accepted for API compatibility but not used
        super().__init__(api_key)

    # ------------------------------------------------------------------
    # Engine implementation
    # ------------------------------------------------------------------

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Launch a stealth browser context, navigate to URL, return ScrapeResult."""
        _load_sdk()  # raises ImportError with hint if missing

        launch_fn = launch_context_async
        sdk_kwargs = _adapt(opts)

        timeout_ms = int(opts.timeout_s * 1000)
        wait_until = "load"

        logger.debug("cloakbrowser launch url=%s kwargs=%s", url, sdk_kwargs)

        ctx = await launch_fn(**sdk_kwargs)
        try:
            page = await ctx.new_page()

            # wait_for_selector handled via Playwright's wait_for_selector after goto
            goto_kwargs: dict[str, Any] = {
                "wait_until": wait_until,
                "timeout": timeout_ms,
            }
            await page.goto(url, **goto_kwargs)

            captcha_key = opts.extra.get("2captcha_api_key") or os.getenv("TWOCAPTCHA_API_KEY")
            captcha_cost = 0.0
            if isinstance(captcha_key, str) and captcha_key.strip():
                try:
                    captcha_cost = await _solve_aws_waf(page, url, captcha_key, opts.timeout_s)
                except Exception:
                    logger.warning("2captcha AWS WAF solve failed; continuing engine ladder")

            if opts.wait_for_selector:
                await page.wait_for_selector(
                    opts.wait_for_selector,
                    timeout=timeout_ms,
                )
            elif opts.wait_ms != _DEFAULT_OPTS.wait_ms:
                await asyncio.sleep(opts.wait_ms / 1000)

            html: str = await page.content()
            text, markdown = html_to_both(html, base_url=url)

            screenshot_b64: str | None = None
            if opts.take_screenshot:
                png_bytes: bytes = await page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")

        finally:
            await ctx.close()

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=html,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=captcha_cost,
            screenshot_b64=screenshot_b64,
        )


__all__ = ["CloakBrowserEngine"]
