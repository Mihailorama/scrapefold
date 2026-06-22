"""Tests for PixelRagEngine.

The tests use a fake ``pixelshot`` executable and never call real PixelRAG,
Chromium, or the network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scrapefold.engines.base import EngineError
from scrapefold.options import ScrapeOptions


def _fake_pixelshot(tmp_path: Path, *, exit_code: int = 0) -> Path:
    script = tmp_path / "pixelshot"
    script.write_text(
        f"""#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

args = sys.argv[1:]
if {exit_code!r}:
    print("pixelshot fake failure", file=sys.stderr)
    raise SystemExit({exit_code!r})

try:
    output = args[args.index("--output") + 1]
except ValueError:
    output = args[args.index("-o") + 1]

out_dir = Path(output)
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "argv.json").write_text(json.dumps(args))
tile_dir = out_dir / "example.png.tiles"
tile_dir.mkdir(parents=True, exist_ok=True)
(tile_dir / "tile_0000.jpg").write_bytes(b"fake-jpeg-zero")
(tile_dir / "tile_0001.jpg").write_bytes(b"fake-jpeg-one")
(tile_dir / "tiles.json").write_text(json.dumps({{
    "url": args[0],
    "page_height": 1234,
    "tiles": ["tile_0000.jpg", "tile_0001.jpg"],
    "complete": True,
}}))
print(tile_dir)
""",
    )
    script.chmod(script.stat().st_mode | 0o111)
    return script


async def test_pixelrag_capture_runs_pixelshot_and_returns_tile_manifest(tmp_path: Path) -> None:
    from scrapefold.engines.pixelrag import PixelRagEngine

    binary = _fake_pixelshot(tmp_path)
    output_dir = tmp_path / "captures"
    opts = ScrapeOptions(
        take_screenshot=True,
        wait_until="networkidle",
        timeout_s=10,
        extra={
            "pixelrag_binary": str(binary),
            "pixelrag_output_dir": str(output_dir),
            "pixelrag_backend": "cdp",
            "pixelrag_workers": 2,
            "pixelrag_tile_height": 1024,
            "pixelrag_quality": 70,
            "pixelrag_viewport_width": 1200,
        },
    )

    result = await PixelRagEngine().scrape("https://example.com/page", opts)

    argv = json.loads((output_dir / "argv.json").read_text())
    assert argv[:3] == ["https://example.com/page", "--output", str(output_dir)]
    assert "--backend" in argv
    assert "cdp" in argv
    assert "--workers" in argv
    assert "2" in argv
    assert "--tile-height" in argv
    assert "1024" in argv
    assert "--quality" in argv
    assert "70" in argv
    assert "--viewport-width" in argv
    assert "1200" in argv
    assert "--wait-network-idle" in argv

    assert result.engine == "pixelrag"
    assert result.html is None
    assert result.cost_usd == 0.0
    assert result.screenshot_b64
    assert "PixelRAG captured 2 screenshot tile(s)" in result.text
    assert "tile_0000.jpg" in result.markdown
    assert result.json is not None
    assert result.json["tile_count"] == 2
    assert result.json["tile_dirs"][0]["manifest"]["url"] == "https://example.com/page"
    assert result.meta["output_dir"] == str(output_dir)


async def test_pixelrag_reader_turns_tiles_into_markdown_and_json(tmp_path: Path) -> None:
    from scrapefold.engines.pixelrag import PixelRagEngine

    calls: list[tuple[str, bytes, str]] = []

    async def reader(prompt: str, image_bytes: bytes, mime_type: str) -> str:
        calls.append((prompt, image_bytes, mime_type))
        if len(calls) == 1:
            return json.dumps(
                {
                    "markdown": "# Example profile\nName: Ada Lovelace",
                    "data": {"name": "Ada Lovelace"},
                }
            )
        return "## Details\n- Role: mathematician"

    binary = _fake_pixelshot(tmp_path)
    output_dir = tmp_path / "captures"
    opts = ScrapeOptions(
        extra={
            "pixelrag_binary": str(binary),
            "pixelrag_output_dir": str(output_dir),
            "pixelrag_reader": reader,
        },
    )

    result = await PixelRagEngine().scrape("https://example.com/profile", opts)

    assert len(calls) == 2
    assert "Convert this screenshot tile into clean markdown" in calls[0][0]
    assert calls[0][1] == b"fake-jpeg-zero"
    assert calls[0][2] == "image/jpeg"
    assert calls[1][1] == b"fake-jpeg-one"
    assert result.engine == "pixelrag"
    assert result.text.startswith("Example profile")
    assert "PixelRAG captured" not in result.text
    assert "# Example profile" in result.markdown
    assert "## Details" in result.markdown
    assert result.json is not None
    assert result.json["mode"] == "read"
    assert result.json["reader"]["outputs"][0]["json"]["data"]["name"] == "Ada Lovelace"
    assert result.json["reader"]["outputs"][1]["markdown"] == "## Details\n- Role: mathematician"


async def test_pixelrag_pixelshot_failure_raises_engine_error(tmp_path: Path) -> None:
    from scrapefold.engines.pixelrag import PixelRagEngine

    binary = _fake_pixelshot(tmp_path, exit_code=7)
    opts = ScrapeOptions(extra={"pixelrag_binary": str(binary)})

    with pytest.raises(EngineError) as exc_info:
        await PixelRagEngine().scrape("https://example.com/page", opts)

    assert exc_info.value.engine == "pixelrag"
    assert "pixelshot exited with 7" in str(exc_info.value)
    assert "pixelshot fake failure" in str(exc_info.value)


def test_pixelrag_availability_checks_pixelshot_binary(tmp_path: Path) -> None:
    from scrapefold.engines.pixelrag import PixelRagEngine

    assert PixelRagEngine(binary=str(_fake_pixelshot(tmp_path))).is_available() is True
    assert PixelRagEngine(binary="definitely-not-a-real-pixelshot").is_available() is False


def test_pixelrag_binary_can_be_read_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIXELRAG_BIN", str(_fake_pixelshot(tmp_path)))
    from scrapefold.engines.pixelrag import PixelRagEngine

    assert PixelRagEngine().is_available() is True


def test_pixelrag_engine_registered() -> None:
    from scrapefold.engines import get_engine, list_engine_names

    assert "pixelrag" in list_engine_names()
    assert get_engine("pixelrag").__name__ == "PixelRagEngine"


def test_fake_pixelshot_is_executable(tmp_path: Path) -> None:
    binary = _fake_pixelshot(tmp_path)

    assert os.access(binary, os.X_OK)
