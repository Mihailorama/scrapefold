"""Tests for one-click MCP registration (`scrapefold install`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scrapefold import install as install_mod
from scrapefold.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# 1. Generic config shape
# ---------------------------------------------------------------------------


def test_mcp_config_shape() -> None:
    config = install_mod.mcp_config()
    assert config == {"mcpServers": {"scrapefold": {"command": "scrapefold-mcp", "args": []}}}


# ---------------------------------------------------------------------------
# 2. plan_install per client
# ---------------------------------------------------------------------------


def test_plan_install_claude_prints_command_when_cli_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_mod.shutil, "which", lambda _name: None)
    plan = install_mod.plan_install("claude")
    assert plan.kind == "print"
    assert plan.argv == ("claude", "mcp", "add", "scrapefold", "--", "scrapefold-mcp")


def test_plan_install_claude_runs_when_cli_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    plan = install_mod.plan_install("claude")
    assert plan.kind == "run"


def test_plan_install_cursor_targets_home_config(tmp_path: Path) -> None:
    plan = install_mod.plan_install("cursor", home=tmp_path)
    assert plan.kind == "merge-json"
    assert plan.path == tmp_path / ".cursor" / "mcp.json"


def test_plan_install_unknown_client_raises() -> None:
    with pytest.raises(ValueError, match="unknown client"):
        install_mod.plan_install("emacs")


# ---------------------------------------------------------------------------
# 3. merge_mcp_json
# ---------------------------------------------------------------------------


def test_merge_mcp_json_creates_file(tmp_path: Path) -> None:
    path = tmp_path / ".cursor" / "mcp.json"
    changed = install_mod.merge_mcp_json(path)
    assert changed is True
    data = json.loads(path.read_text())
    assert data["mcpServers"]["scrapefold"]["command"] == "scrapefold-mcp"


def test_merge_mcp_json_preserves_existing_servers(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "other-mcp"}}}))
    changed = install_mod.merge_mcp_json(path)
    assert changed is True
    data = json.loads(path.read_text())
    assert data["mcpServers"]["other"] == {"command": "other-mcp"}
    assert data["mcpServers"]["scrapefold"] == {"command": "scrapefold-mcp", "args": []}


def test_merge_mcp_json_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    assert install_mod.merge_mcp_json(path) is True
    assert install_mod.merge_mcp_json(path) is False


# ---------------------------------------------------------------------------
# 4. apply_plan
# ---------------------------------------------------------------------------


def test_apply_plan_run_respects_print_only() -> None:
    plan = install_mod.InstallPlan(client="claude", kind="run", argv=("claude", "mcp", "add"))
    message = install_mod.apply_plan(plan, print_only=True)
    assert "Would run" in message
    assert "claude mcp add" in message


def test_apply_plan_run_invokes_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        install_mod.subprocess, "run", lambda argv, check: calls.append(tuple(argv))
    )
    plan = install_mod.InstallPlan(client="claude", kind="run", argv=("claude", "mcp", "add"))
    message = install_mod.apply_plan(plan)
    assert calls == [("claude", "mcp", "add")]
    assert "Registered" in message


def test_apply_plan_generic_prints_config() -> None:
    plan = install_mod.InstallPlan(client="generic", kind="print")
    message = install_mod.apply_plan(plan)
    assert '"mcpServers"' in message
    assert '"scrapefold-mcp"' in message


# ---------------------------------------------------------------------------
# 5. CLI wiring
# ---------------------------------------------------------------------------


def test_install_command_json_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["install", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["mcpServers"]["scrapefold"]["command"] == "scrapefold-mcp"


def test_install_command_generic_default(runner: CliRunner) -> None:
    result = runner.invoke(app, ["install"])
    assert result.exit_code == 0
    assert "mcpServers" in result.stdout


def test_install_command_unknown_client_exit_1(runner: CliRunner) -> None:
    result = runner.invoke(app, ["install", "emacs"])
    assert result.exit_code == 1
    assert "unknown client" in result.output


def test_install_command_cursor_print_only(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_mod.Path, "home", classmethod(lambda _cls: tmp_path))
    result = runner.invoke(app, ["install", "cursor", "--print-only"])
    assert result.exit_code == 0
    assert "Would merge" in result.stdout
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_install_command_cursor_writes_config(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_mod.Path, "home", classmethod(lambda _cls: tmp_path))
    result = runner.invoke(app, ["install", "cursor"])
    assert result.exit_code == 0
    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert data["mcpServers"]["scrapefold"]["command"] == "scrapefold-mcp"
