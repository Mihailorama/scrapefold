"""Tests for self-update helpers and the `scrapefold update` CLI command."""

from __future__ import annotations

import json
import sys

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from scrapefold import update as update_mod
from scrapefold.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------


def test_is_newer_numeric_compare() -> None:
    assert update_mod.is_newer("0.5.0", "0.4.0") is True
    assert update_mod.is_newer("0.4.0", "0.4.0") is False
    assert update_mod.is_newer("0.4.0", "0.10.0") is False
    assert update_mod.is_newer("1.0.0", "0.99.99") is True


def test_build_update_argv_plain() -> None:
    argv = update_mod.build_update_argv()
    assert argv == [sys.executable, "-m", "pip", "install", "--upgrade", "scrapefold"]


def test_build_update_argv_with_extras() -> None:
    argv = update_mod.build_update_argv("mcp, firecrawl")
    assert argv[-1] == "scrapefold[mcp,firecrawl]"


def test_latest_version_reads_pypi_json(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=update_mod.PYPI_JSON_URL, json={"info": {"version": "9.9.9"}})
    assert update_mod.latest_version() == "9.9.9"


# ---------------------------------------------------------------------------
# 2. `scrapefold update --check`
# ---------------------------------------------------------------------------


def test_update_check_reports_newer_version(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update_mod, "latest_version", lambda: "99.0.0")
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "update available" in result.stdout
    assert "99.0.0" in result.stdout


def test_update_check_up_to_date(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    import scrapefold

    monkeypatch.setattr(update_mod, "latest_version", lambda: scrapefold.__version__)
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 0
    assert "up to date" in result.stdout


def test_update_check_json(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_mod, "latest_version", lambda: "99.0.0")
    result = runner.invoke(app, ["update", "--check", "--json"])
    data = json.loads(result.stdout)
    assert data["latest"] == "99.0.0"
    assert data["update_available"] is True


def test_update_check_network_error_exit_1(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> str:
        raise OSError("offline")

    monkeypatch.setattr(update_mod, "latest_version", _boom)
    result = runner.invoke(app, ["update", "--check"])
    assert result.exit_code == 1
    assert "update check failed" in result.output


# ---------------------------------------------------------------------------
# 3. `scrapefold update` runs pip
# ---------------------------------------------------------------------------


def test_update_invokes_pip(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, check: calls.append(list(argv)))

    result = runner.invoke(app, ["update", "--extras", "mcp"])
    assert result.exit_code == 0
    assert calls == [[sys.executable, "-m", "pip", "install", "--upgrade", "scrapefold[mcp]"]]
    assert "scrapefold doctor" in result.stdout
