"""Maxun engine for scrapefold.

Maxun (https://github.com/getmaxun/maxun) is an open-source, self-hostable
no-code web data extraction platform. Users record browser workflows
("robots") in Maxun's UI; each robot captures structured lists/fields, page
markdown/HTML, screenshots, and more. This engine triggers a robot run over
Maxun's REST API and maps the run output into a ``ScrapeResult``.

Unlike URL-first engines, a Maxun robot is bound to the site it was recorded
on, so the caller must say *which* robot to run via
``opts.extra["maxun_robot_id"]``. Without it a ``ValueError`` is raised
(wrapped as ``EngineError``) so the router escalates to another engine.

Pinned API contract (verified against getmaxun/maxun@master, 2026-06):
  Auth     : ``x-api-key: <api_key>`` request header
  Base URL : self-hosted backend, default ``http://localhost:8080``
             (override via ``MAXUN_BASE_URL`` env or ``extra["maxun_base_url"]``)
  Run      : ``POST {base}/api/robots/{id}/runs``
             body ``{"formats": [...], "promptInstructions": "..."}``
             — **synchronous**: the server blocks until the workflow finishes
             and returns the completed run, so ``opts.timeout_s`` must cover
             the whole robot run (default here: 300 s).
  Duplicate: ``POST {base}/api/robots/{id}/duplicate`` body ``{"targetUrl": url}``
             → ``{"robot": {"id": ...}}`` (only when
             ``extra["maxun_duplicate_for_url"]`` is truthy).
  Response : ``{"statusCode": 200, "messageCode": "success",
                "run": {"status": "success", "runId": ...,
                        "data": {"textData": {}, "listData": {},
                                 "crawlData": {}, "searchData": {},
                                 "markdown": "", "html": "",
                                 "summary": null, "promptResult": null},
                        "screenshots": [...]}}``

Native parameter surface
------------------------
==============================  ==========================================  =======================
Native parameter                Unified option                              Default
==============================  ==========================================  =======================
robot id (URL path)             ``extra["maxun_robot_id"]`` (required)      — (ValueError if unset)
``formats`` (run body)          ``output_format`` + ``take_screenshot``     ``["markdown", "html"]``
``promptInstructions`` (body)   ``extra["maxun_promptInstructions"]``       not sent
``targetUrl`` (duplicate body)  scrape ``url`` when                         duplication off
                                ``extra["maxun_duplicate_for_url"]`` set
base URL                        ``extra["maxun_base_url"]`` /               ``http://localhost:8080``
                                ``MAXUN_BASE_URL`` env
HTTP timeout                    ``timeout_s``                               300 s
==============================  ==========================================  =======================

Any other ``maxun_``-prefixed extra is stripped of its prefix and merged
into the run request body (escape hatch for future Maxun parameters).

Output mapping
--------------
- ``data.markdown`` / ``data.html`` → the ``markdown`` / ``html`` slots
  (``text`` + ``markdown`` post-converted when only HTML came back).
- Non-empty ``textData`` / ``listData`` / ``crawlData`` / ``searchData``
  (the robot's captured structured output) → ``ScrapeResult.json`` as a
  ``{slot_name: payload}`` dict.
- ``summary`` / ``promptResult`` → ``meta`` (LLM-generated, keep separate
  from scraped content).
- ``screenshots`` → ``meta["screenshots"]`` verbatim (Maxun stores uploads
  in its own bucket and returns references); when an entry carries inline
  base64 under ``"data"``, it also populates ``screenshot_b64``.

Cost: $0 — Maxun is self-hosted (its cloud is billed separately; we do not
model it).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from scrapefold.engines.base import EngineCapabilities, ScrapeEngine
from scrapefold.html_to_text import html_to_both, json_to_scrape_text, markdown_to_text
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:8080"

_DEFAULT_TIMEOUT_S = 300

# Keys in run["data"] that hold the robot's captured structured output.
_STRUCTURED_KEYS = ("textData", "listData", "crawlData", "searchData")

# extra["maxun_*"] keys that route the call rather than feed the run body.
_ROUTING_KEYS = ("robot_id", "base_url", "duplicate_for_url", "formats")


def _resolve_robot_id(opts: ScrapeOptions) -> str:
    """Return the robot id from ``extra["maxun_robot_id"]``.

    Raises ``ValueError`` when missing — a Maxun robot is recorded per-site,
    so there is no sensible URL→robot default. The router treats the wrapped
    ``EngineError`` as a normal failure and escalates.
    """
    robot_id = opts.extra.get("maxun_robot_id") if opts.extra else None
    if not robot_id:
        raise ValueError(
            "maxun engine needs a robot to run; set one via opts.extra['maxun_robot_id']"
        )
    return str(robot_id)


def _resolve_formats(opts: ScrapeOptions) -> list[str]:
    """Map ``output_format`` (+ ``take_screenshot``) to Maxun's ``formats`` list.

    ``extra["maxun_formats"]`` overrides the mapping entirely. Structured data
    (list/field capture) is produced by the robot regardless of ``formats``,
    so ``output_format="json"`` still requests markdown for the text slots.
    Maxun's ``"text"`` format is not exposed in its API run response, so text
    is always post-converted locally.
    """
    override = opts.extra.get("maxun_formats") if opts.extra else None
    if override:
        return [str(f) for f in override]
    formats = ["html"] if opts.output_format == "html" else ["markdown", "html"]
    if opts.take_screenshot:
        formats.append("screenshot-visible")
    return formats


def _build_run_body(opts: ScrapeOptions) -> dict[str, Any]:
    """Build the ``POST /runs`` JSON body: formats + ``maxun_*`` passthrough."""
    body: dict[str, Any] = {"formats": _resolve_formats(opts)}
    passthrough = strip_extra_prefix(opts.extra, "maxun_")
    for key in _ROUTING_KEYS:
        passthrough.pop(key, None)
    body.update(passthrough)
    return body


def _extract_screenshot_b64(screenshots: list[Any]) -> str | None:
    """Best-effort inline base64 from the run's screenshots list.

    After upload Maxun usually returns storage references (URL strings);
    only pre-upload / inline shapes carry ``{"data": "<b64>"}``.
    """
    for shot in screenshots:
        if isinstance(shot, dict) and isinstance(shot.get("data"), str) and shot["data"]:
            return shot["data"]
    return None


class MaxunEngine(ScrapeEngine):
    """Self-hosted Maxun robot runner.

    Runs the robot named by ``opts.extra["maxun_robot_id"]`` against a Maxun
    instance (``MAXUN_BASE_URL``, default ``http://localhost:8080``) and maps
    the completed run to a ``ScrapeResult``. With
    ``extra["maxun_duplicate_for_url"]=True`` the robot is first duplicated
    to target the scraped ``url`` (Maxun's documented way to retarget a
    recorded robot) — note this persists a new robot on the Maxun server.

    API key is read from the constructor argument or the ``MAXUN_API_KEY``
    environment variable (generate one in Maxun's UI under API Key).
    """

    NAME = "maxun"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=True,
        screenshot=True,
        requires_api_key=True,
        estimated_cost_usd=0.0,
        billing_unit="call",
        free_tier=True,
        output_native_markdown=True,
        default_timeout_s=_DEFAULT_TIMEOUT_S,
        avg_response_mb_estimate=0.5,  # robot-run JSON payload
    )
    SUPPORTED_OPTIONS = frozenset({"output_format", "take_screenshot", "timeout_s", "extra"})

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        super().__init__(api_key or os.getenv("MAXUN_API_KEY"))
        self.base_url = (base_url or os.getenv("MAXUN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    def _resolve_base_url(self, opts: ScrapeOptions) -> str:
        override = opts.extra.get("maxun_base_url") if opts.extra else None
        return str(override).rstrip("/") if override else self.base_url

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        """Run the robot synchronously and map its output to a ScrapeResult."""
        base_url = self._resolve_base_url(opts)
        robot_id = _resolve_robot_id(opts)
        headers = {"x-api-key": self.api_key or "", "Content-Type": "application/json"}

        # The run endpoint blocks until the robot finishes; timeout_s of 60 is
        # scrapefold's generic default and too tight for a browser workflow,
        # so only honor explicitly larger/smaller caller values.
        timeout_s = (
            max(opts.timeout_s, _DEFAULT_TIMEOUT_S) if opts.timeout_s == 60 else opts.timeout_s
        )

        async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
            if opts.extra.get("maxun_duplicate_for_url"):
                dup_response = await client.post(
                    f"{base_url}/api/robots/{robot_id}/duplicate",
                    json={"targetUrl": url},
                    headers=headers,
                )
                dup_response.raise_for_status()
                robot_id = str(dup_response.json()["robot"]["id"])
                logger.debug("maxun duplicated robot for url=%s -> robot_id=%s", url, robot_id)

            body = _build_run_body(opts)
            logger.debug("maxun run robot_id=%s body=%s", robot_id, body)
            response = await client.post(
                f"{base_url}/api/robots/{robot_id}/runs",
                json=body,
                headers=headers,
            )

        # Surface API failures (401/403 bad key, 404 unknown robot, 500 run
        # failure) as EngineError so the router can escalate.
        response.raise_for_status()

        run = response.json().get("run") or {}
        status = run.get("status")
        if status != "success":
            raise RuntimeError(
                f"Maxun run {run.get('runId') or '?'} finished with status {status!r}"
            )

        data = run.get("data") or {}
        raw_markdown: str = data.get("markdown") or ""
        raw_html: str | None = data.get("html") or None
        structured: dict[str, Any] = {key: data[key] for key in _STRUCTURED_KEYS if data.get(key)}

        if raw_html:
            text_out, converted_md = html_to_both(raw_html, base_url=url)
            markdown_out = raw_markdown or converted_md
        elif raw_markdown:
            markdown_out = raw_markdown
            text_out = markdown_to_text(raw_markdown)
        elif structured:
            text_out, markdown_out = json_to_scrape_text(structured)
        else:
            raise RuntimeError(
                f"Maxun run {run.get('runId') or '?'} produced no data for URL: {url}"
            )

        screenshots = run.get("screenshots") or []
        meta: dict[str, Any] = {
            "robot_id": robot_id,
            "run_id": run.get("runId"),
            "status": status,
        }
        if screenshots:
            meta["screenshots"] = screenshots
        if data.get("summary"):
            meta["summary"] = data["summary"]
        if data.get("promptResult"):
            meta["prompt_result"] = data["promptResult"]

        return ScrapeResult(
            url=url,
            text=text_out,
            markdown=markdown_out,
            html=raw_html,
            engine=self.NAME,
            elapsed_ms=0,  # base class patches this
            cost_usd=0.0,
            json=structured or None,
            screenshot_b64=_extract_screenshot_b64(screenshots),
            meta=meta,
        )


__all__ = ["DEFAULT_BASE_URL", "MaxunEngine"]
