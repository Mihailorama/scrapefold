"""Tests for the subprocess-driven browser engines: lightpanda and obscura.

Offline: ``run_cli`` is patched for adapter/result tests; the real subprocess
path runs against a tiny fake binary (a shell script), never the network.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from scrapefold.engines import get_engine
from scrapefold.engines._cli import run_cli
from scrapefold.engines.base import EngineError, RedirectScopeViolation
from scrapefold.engines.lightpanda import LightpandaEngine
from scrapefold.engines.lightpanda import _adapt as lp_adapt
from scrapefold.engines.obscura import ObscuraEngine, _serve_args, _timeout_s
from scrapefold.options import ScrapeOptions

_HTML = "<html><body><h1>Hello Panda</h1><p>rendered by js</p></body></html>"


def _lp_json(**over: Any) -> bytes:
    data = {"url": "https://example.com/", "http_status": 200, "content": _HTML, "error": None}
    data.update(over)
    return json.dumps(data).encode()


def _patch_cli(module: str, out: bytes) -> Any:
    return patch(f"scrapefold.engines.{module}.run_cli", AsyncMock(return_value=out))


def _fake_binary(tmp_path: Path, body: str) -> str:
    path = tmp_path / "fakebin"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


# --- registry / availability -------------------------------------------------


def test_registered() -> None:
    assert get_engine("lightpanda") is LightpandaEngine
    assert get_engine("obscura") is ObscuraEngine


@pytest.mark.parametrize(
    "engine_cls,env", [(LightpandaEngine, "LIGHTPANDA_BIN"), (ObscuraEngine, "OBSCURA_BIN")]
)
def test_availability_follows_binary(
    engine_cls: type, env: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv(env, raising=False)
    assert engine_cls().is_available() is False
    monkeypatch.setenv(env, _fake_binary(tmp_path, "exit 0"))
    assert engine_cls().is_available() is True


# --- adapters ----------------------------------------------------------------


def test_lightpanda_adapter_maps_unified_options() -> None:
    opts = ScrapeOptions(
        wait_until="networkidle",
        wait_for_selector="#app",
        proxy="http://u:p@proxy:8080",
        language="de-DE,de;q=0.9",
        custom_headers={"X-Test": "1"},
        cookies={"sid": "abc"},
        user_agent="Mozilla/5.0 Chrome",  # unsupported: Lightpanda forbids spoofing
        timeout_s=20,
    )
    args = lp_adapt(LightpandaEngine()._strip_unsupported(opts))
    assert args[args.index("--wait-until") + 1] == "networkidle"
    assert args[args.index("--wait-selector") + 1] == "#app"
    assert args[args.index("--http-proxy") + 1] == "http://u:p@proxy:8080"
    assert args[args.index("--locale") + 1] == "de-DE"
    assert "X-Test: 1" in args and "Cookie: sid=abc" in args
    assert not any("Accept-Language" in a or "Mozilla" in a for a in args)
    assert args[args.index("--http-timeout") + 1] == "20000"


def test_obscura_serve_args_and_stealth_is_opt_in() -> None:
    args = _serve_args(ScrapeOptions(user_agent="UA/1", proxy="http://p:1"), "/tmp/r.json")
    assert args[:3] == ["serve", "--port", "0"]
    assert args[args.index("--user-agent") + 1] == "UA/1"
    assert args[args.index("--proxy") + 1] == "http://p:1"
    assert args[args.index("--ready-file") + 1] == "/tmp/r.json"
    assert "--stealth" not in args
    assert "--stealth" in _serve_args(ScrapeOptions(extra={"obscura_stealth": True}), "r")


def test_obscura_timeout_is_capped() -> None:
    assert _timeout_s(ScrapeOptions(timeout_s=60)) == 25
    assert _timeout_s(ScrapeOptions(timeout_s=10)) == 10
    assert _timeout_s(ScrapeOptions(timeout_s=60, extra={"obscura_timeout_s": 40})) == 40


# --- result mapping ------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "engine_cls,module,out",
    [
        (LightpandaEngine, "lightpanda", _lp_json()),
    ],
)
async def test_fetch_populates_all_slots(
    engine_cls: type, module: str, out: bytes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "LIGHTPANDA_BIN" if module == "lightpanda" else "OBSCURA_BIN",
        _fake_binary(tmp_path, "exit 0"),
    )
    with _patch_cli(module, out):
        result = await engine_cls().scrape("https://example.com/")
    assert result.engine == module
    assert result.html == _HTML
    assert "Hello Panda" in result.text and "Hello Panda" in result.markdown
    assert result.cost_usd == 0.0


@pytest.mark.anyio
async def test_lightpanda_navigation_error_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LIGHTPANDA_BIN", _fake_binary(tmp_path, "exit 0"))
    out = _lp_json(error="CouldntResolveHost", http_status=0)
    with _patch_cli("lightpanda", out), pytest.raises(EngineError, match="CouldntResolveHost"):
        await LightpandaEngine().scrape("https://x.invalid/")


@pytest.mark.anyio
async def test_lightpanda_status_lands_in_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LIGHTPANDA_BIN", _fake_binary(tmp_path, "exit 0"))
    with _patch_cli("lightpanda", _lp_json(http_status=404)):
        result = await LightpandaEngine().scrape("https://example.com/")
    assert result.status_code == 404  # detection.is_suspicious escalates on >= 400


@pytest.mark.anyio
@pytest.mark.parametrize(
    "engine_cls,module,out",
    [
        (LightpandaEngine, "lightpanda", _lp_json(url="https://evil.test/")),
    ],
)
async def test_offhost_redirect_violates_crawl_scope(
    engine_cls: type, module: str, out: bytes, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "LIGHTPANDA_BIN" if module == "lightpanda" else "OBSCURA_BIN",
        _fake_binary(tmp_path, "exit 0"),
    )
    scope = {
        "same_host_redirect_scope": {"root": "https://example.com/", "follow_subdomains": False}
    }
    with _patch_cli(module, out), pytest.raises(RedirectScopeViolation):
        await engine_cls().scrape("https://example.com/", ScrapeOptions(extra=scope))


# --- obscura over CDP (fake Playwright objects) ----------------------------------


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def all_headers(self) -> dict[str, str]:
        return {"x-datadome": "protected"}


class _FakePage:
    def __init__(self, final_url: str, status: int, exc: Exception | None) -> None:
        self.url = final_url
        self._status = status
        self._exc = exc
        self.goto_kwargs: dict[str, Any] = {}

    async def goto(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.goto_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._status)

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> None:
        return None

    async def content(self) -> str:
        return _HTML


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.headers: dict[str, str] = {}
        self.added: list[dict[str, str]] = []

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        self.headers = headers

    async def add_cookies(self, cookies: list[dict[str, str]]) -> None:
        self.added = cookies

    async def new_page(self) -> _FakePage:
        return self.page

    async def cookies(self) -> list[dict[str, str]]:
        return [{"name": "datadome", "value": "x"}]


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.contexts = [_FakeContext(page)]


# Stand-in for playwright.async_api.TimeoutError (matched by class name).
_PwTimeout = type("TimeoutError", (Exception,), {})


@pytest.mark.anyio
async def test_obscura_page_maps_status_headers_cookies() -> None:
    page = _FakePage("https://example.com/", 403, None)
    browser = _FakeBrowser(page)
    opts = ScrapeOptions(language="de-DE", custom_headers={"X-T": "1"}, cookies={"sid": "a"})
    result = await ObscuraEngine()._load_page(browser, "https://example.com/", opts)
    ctx = browser.contexts[0]
    assert ctx.headers == {"Accept-Language": "de-DE", "X-T": "1"}
    assert ctx.added == [{"name": "sid", "value": "a", "domain": "example.com", "path": "/"}]
    assert result.status_code == 403
    assert result.meta["cookies"] == {"datadome": "x"}
    assert result.meta["headers"] == {"x-datadome": "protected"}
    assert "Hello Panda" in result.markdown
    assert page.goto_kwargs["timeout"] == 25_000


@pytest.mark.anyio
async def test_obscura_timeout_returns_partial_dom() -> None:
    page = _FakePage("https://example.com/", 200, _PwTimeout("nav timeout"))
    result = await ObscuraEngine()._load_page(
        _FakeBrowser(page), "https://example.com/", ScrapeOptions()
    )
    assert result.meta["timed_out"] is True
    assert result.status_code is None
    assert result.html == _HTML


@pytest.mark.anyio
async def test_obscura_offhost_redirect_violates_crawl_scope() -> None:
    page = _FakePage("https://evil.test/", 200, None)
    scope = {
        "same_host_redirect_scope": {"root": "https://example.com/", "follow_subdomains": False}
    }
    with pytest.raises(RedirectScopeViolation):
        await ObscuraEngine()._load_page(
            _FakeBrowser(page), "https://example.com/", ScrapeOptions(extra=scope)
        )


# --- real subprocess plumbing ----------------------------------------------------


@pytest.mark.anyio
async def test_run_cli_returns_stdout_and_raises_on_failure(tmp_path: Path) -> None:
    ok = _fake_binary(tmp_path, 'echo "$LP_FLAG"')
    assert (await run_cli([ok], timeout_s=5, env={"LP_FLAG": "set"})).strip() == b"set"
    (tmp_path / "fakebin").unlink()
    bad = _fake_binary(tmp_path, "echo nope >&2; exit 3")
    with pytest.raises(RuntimeError, match="exit 3: nope"):
        await run_cli([bad], timeout_s=5)


@pytest.mark.anyio
async def test_run_cli_kills_on_timeout(tmp_path: Path) -> None:
    slow = _fake_binary(tmp_path, "exec sleep 30")
    with pytest.raises(TimeoutError):
        await run_cli([slow], timeout_s=0.3)
