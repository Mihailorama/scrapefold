"""Shared plumbing for engines that drive a local browser binary via subprocess.

Used by ``lightpanda`` and ``obscura``. Both ship as standalone binaries (no
Python SDK), so "SDK missing" here means "binary not on PATH" and is reported
through ``is_available()`` rather than an ImportError.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Mapping
from typing import Any

from scrapefold._host_utils import same_host
from scrapefold.engines.base import RedirectScopeViolation


def resolve_binary(override: Any, env_var: str, default_name: str) -> str | None:
    """Return an executable path: ``extra`` override > ``$env_var`` > ``default_name`` on PATH."""
    candidate = override or os.environ.get(env_var) or default_name
    return shutil.which(str(candidate))


async def run_cli(
    argv: list[str], *, timeout_s: float, env: Mapping[str, str] | None = None
) -> bytes:
    """Run ``argv``, return stdout. Kills the process on timeout; raises on non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **env} if env else None,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"{os.path.basename(argv[0])} exceeded {timeout_s}s") from None
    if proc.returncode != 0:
        tail = err.decode(errors="replace").strip()[-500:]
        raise RuntimeError(f"exit {proc.returncode}: {tail}")
    return out


def check_redirect_scope(engine: str, final_url: str | None, extra: Mapping[str, Any]) -> None:
    """Raise ``RedirectScopeViolation`` when a crawl-scoped fetch landed off-host."""
    scope = extra.get("same_host_redirect_scope")
    if not scope or not final_url:
        return
    if not same_host(final_url, scope["root"], bool(scope.get("follow_subdomains", False))):
        raise RedirectScopeViolation(
            engine=engine,
            message=f"redirect left same_host_redirect_scope (root={scope['root']!r})",
            target=final_url,
        )


__all__ = ["check_redirect_scope", "resolve_binary", "run_cli"]
