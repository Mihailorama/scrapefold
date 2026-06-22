"""PixelRagEngine - local PixelRAG screenshot-tile capture via ``pixelshot``.

PixelRAG's public capture path is the ``pixelshot`` command from the
``pixelrag`` package. It renders URLs, PDFs, HTML files, and images to tiled
JPEG screenshots plus a ``tiles.json`` manifest. This engine wraps that local
tool as a visual parser stage and returns the tile manifest in
``ScrapeResult.json``. When ``pixelrag_reader`` is provided, each tile is also
sent to that injected VLM/OCR callable and the engine returns reader-produced
markdown/text.

Native parameter surface
------------------------

==============================  ===============================  ==============================
PixelRAG field                  Unified source                   Notes
==============================  ===============================  ==============================
``pixelshot`` binary            ``PIXELRAG_BIN`` / constructor    Defaults to ``pixelshot``
``INPUT``                       target URL                       URL passed as the first input
``--output``                    ``pixelrag_output_dir``           Defaults to a temp directory
``--backend``                   ``pixelrag_backend``              Defaults to ``cdp``
``--workers``                   ``pixelrag_workers``              Defaults to ``1``
``--tile-height``               ``pixelrag_tile_height``          Defaults to PixelRAG CLI
``--quality``                   ``pixelrag_quality``              Defaults to PixelRAG CLI
``--viewport-width``            ``pixelrag_viewport_width``       Defaults to PixelRAG CLI
``--wait-network-idle``         ``wait_until=networkidle``        Or ``pixelrag_wait_network_idle``
``--dpi``                       ``pixelrag_dpi``                  For PDF inputs
tile VLM/OCR reader             ``pixelrag_reader``               Async callable, no SDK import
reader prompt                   ``pixelrag_reader_prompt``        Overrides the default prompt
==============================  ===============================  ==============================

The hosted PixelRAG search API is intentionally not mapped to ``scrape(url)``
in this first pass because it is query-to-tile retrieval, not URL-to-result
scraping. Use ``pixelrag`` explicitly when the desired output is visual tiles.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

from scrapefold.engines.base import EngineCapabilities, EngineError, ScrapeEngine
from scrapefold.options import ScrapeOptions, strip_extra_prefix
from scrapefold.result import ScrapeResult
from scrapefold.vision import LLMCallable, MimeType, analyze_screenshot_with_llm

_DEFAULT_BINARY = "pixelshot"
_DEFAULT_READER_PROMPT = (
    "Convert this screenshot tile into clean markdown. Preserve visible headings, "
    "tables, lists, form labels, numbers, and meaningful text. Do not add commentary "
    "about the screenshot itself."
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _output_dir(extra: dict[str, Any]) -> Path:
    configured = extra.get("output_dir")
    if configured:
        return Path(str(configured)).expanduser()
    return Path(tempfile.mkdtemp(prefix="scrapefold-pixelrag-"))


def _add_int_flag(command: list[str], flag: str, value: Any) -> None:
    if value is not None:
        command.extend([flag, str(int(value))])


def _build_command(binary: str, url: str, opts: ScrapeOptions, output_dir: Path) -> list[str]:
    extra = strip_extra_prefix(opts.extra, "pixelrag_")
    command = [
        str(extra.get("binary", binary)),
        url,
        "--output",
        str(output_dir),
        "--backend",
        str(extra.get("backend", "cdp")),
    ]
    _add_int_flag(command, "--workers", extra.get("workers", 1))
    _add_int_flag(command, "--tile-height", extra.get("tile_height"))
    _add_int_flag(command, "--quality", extra.get("quality"))
    _add_int_flag(command, "--viewport-width", extra.get("viewport_width"))
    _add_int_flag(command, "--dpi", extra.get("dpi"))
    if _as_bool(extra.get("wait_network_idle", opts.wait_until == "networkidle")):
        command.append("--wait-network-idle")
    return command


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


async def _run_pixelshot(command: list[str], timeout_s: int) -> tuple[str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise EngineError(PixelRagEngine.NAME, f"pixelshot binary not found: {command[0]}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise EngineError(
            PixelRagEngine.NAME,
            f"pixelshot timed out after {timeout_s}s",
        ) from exc

    stdout = _decode(stdout_b)
    stderr = _decode(stderr_b)
    if proc.returncode != 0:
        detail = stderr or stdout or "no stderr"
        raise EngineError(
            PixelRagEngine.NAME,
            f"pixelshot exited with {proc.returncode}: {detail}",
        )
    return stdout, stderr


def _discover_tile_dirs(output_dir: Path, stdout: str) -> list[Path]:
    seen: set[Path] = set()
    tile_dirs: list[Path] = []

    for line in stdout.splitlines():
        candidate = Path(line.strip())
        if not candidate.is_absolute():
            candidate = output_dir / candidate
        if candidate.is_dir() and (candidate / "tiles.json").exists():
            resolved = candidate.resolve()
            if resolved not in seen:
                tile_dirs.append(resolved)
                seen.add(resolved)

    for manifest in sorted(output_dir.rglob("tiles.json")):
        resolved = manifest.parent.resolve()
        if resolved not in seen:
            tile_dirs.append(resolved)
            seen.add(resolved)

    return tile_dirs


def _load_manifest(tile_dir: Path) -> dict[str, Any]:
    with open(tile_dir / "tiles.json", encoding="utf-8") as f:
        manifest = json.load(f)
    return manifest if isinstance(manifest, dict) else {"tiles": []}


def _tile_entries(tile_dir: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    tiles = manifest.get("tiles", [])
    if not isinstance(tiles, list):
        return entries

    for tile in tiles:
        name = str(tile)
        path = (tile_dir / name).resolve()
        entries.append(
            {
                "name": name,
                "path": str(path),
                "uri": path.as_uri(),
            }
        )
    return entries


def _capture_payload(url: str, output_dir: Path, tile_dirs: list[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    tile_count = 0

    for tile_dir in tile_dirs:
        manifest = _load_manifest(tile_dir)
        tiles = _tile_entries(tile_dir, manifest)
        tile_count += len(tiles)
        records.append(
            {
                "path": str(tile_dir),
                "manifest_path": str((tile_dir / "tiles.json").resolve()),
                "manifest": manifest,
                "tiles": tiles,
            }
        )

    return {
        "mode": "capture",
        "source": url,
        "output_dir": str(output_dir),
        "tile_count": tile_count,
        "tile_dirs": records,
    }


def _first_tile_path(payload: dict[str, Any]) -> Path | None:
    tile_dirs = payload.get("tile_dirs")
    if not isinstance(tile_dirs, list):
        return None
    for tile_dir in tile_dirs:
        if not isinstance(tile_dir, dict):
            continue
        tiles = tile_dir.get("tiles")
        if not isinstance(tiles, list):
            continue
        for tile in tiles:
            if not isinstance(tile, dict):
                continue
            path = tile.get("path")
            if isinstance(path, str) and Path(path).exists():
                return Path(path)
    return None


def _screenshot_b64(payload: dict[str, Any]) -> str | None:
    first_tile = _first_tile_path(payload)
    if first_tile is None:
        return None
    return base64.b64encode(first_tile.read_bytes()).decode("ascii")


def _mime_type_for(path: Path) -> MimeType:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _parse_reader_json(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _markdown_from_reader_output(raw: str, parsed: dict[str, Any] | None) -> str:
    if parsed is None:
        return raw.strip()
    for key in ("markdown", "text", "content"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _plain_text_from_markdown(markdown: str) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        while stripped.startswith("#"):
            stripped = stripped[1:].lstrip()
        for prefix in ("- ", "* ", "> "):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :].lstrip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def _iter_tile_payloads(payload: dict[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for tile_dir in payload.get("tile_dirs", []):
        if not isinstance(tile_dir, dict):
            continue
        tiles = tile_dir.get("tiles", [])
        if not isinstance(tiles, list):
            continue
        for tile in tiles:
            if not isinstance(tile, dict):
                continue
            path = tile.get("path")
            name = tile.get("name")
            uri = tile.get("uri")
            if isinstance(path, str) and isinstance(name, str) and isinstance(uri, str):
                entries.append({"path": path, "name": name, "uri": uri})
    return entries


async def _read_tiles_with_reader(
    payload: dict[str, Any],
    reader: LLMCallable,
    *,
    prompt: str,
) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    for tile in _iter_tile_payloads(payload):
        path = Path(tile["path"])
        raw = await analyze_screenshot_with_llm(
            path.read_bytes(),
            reader,
            prompt=prompt,
            mime_type=cast(MimeType, _mime_type_for(path)),
        )
        parsed = _parse_reader_json(raw)
        markdown = _markdown_from_reader_output(raw, parsed)
        outputs.append(
            {
                "tile": tile["name"],
                "path": tile["path"],
                "uri": tile["uri"],
                "raw": raw,
                "markdown": markdown,
                "text": _plain_text_from_markdown(markdown),
                "json": parsed,
            }
        )

    read_payload = dict(payload)
    read_payload["mode"] = "read"
    read_payload["reader"] = {
        "prompt": prompt,
        "output_count": len(outputs),
        "outputs": outputs,
    }
    return read_payload


def _text_from_reader_payload(payload: dict[str, Any]) -> tuple[str, str]:
    reader = payload.get("reader", {})
    outputs = reader.get("outputs", []) if isinstance(reader, dict) else []
    text_parts: list[str] = []
    markdown_parts: list[str] = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        text = output.get("text")
        markdown = output.get("markdown")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())
        if isinstance(markdown, str) and markdown.strip():
            markdown_parts.append(markdown.strip())
    return "\n\n".join(text_parts), "\n\n---\n\n".join(markdown_parts)


def _text_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    source = str(payload["source"])
    output_dir = str(payload["output_dir"])
    tile_count = int(payload["tile_count"])

    text_lines = [
        f"PixelRAG captured {tile_count} screenshot tile(s) for {source}.",
        f"Output directory: {output_dir}",
    ]
    markdown_lines = [
        "# PixelRAG visual capture",
        "",
        f"Source: {source}",
        "",
        f"Output directory: `{output_dir}`",
        "",
        "## Tiles",
    ]

    for tile_dir in payload["tile_dirs"]:
        for tile in tile_dir["tiles"]:
            text_lines.append(f"{tile['name']}: {tile['path']}")
            markdown_lines.append(f"![{tile['name']}]({tile['uri']})")

    return "\n".join(text_lines), "\n".join(markdown_lines)


class PixelRagEngine(ScrapeEngine):
    """PixelRAG visual capture engine using the local ``pixelshot`` binary."""

    NAME = "pixelrag"
    CAPABILITIES = EngineCapabilities(
        js_rendering=True,
        stealth=False,
        screenshot=True,
        requires_api_key=False,
        estimated_cost_usd=0.0,
        billing_unit="call",
        avg_response_mb_estimate=15.0,
        output_native_markdown=False,
    )
    SUPPORTED_OPTIONS = frozenset(
        {"render_js", "wait_until", "take_screenshot", "timeout_s", "extra"}
    )

    def __init__(self, binary: str | None = None) -> None:
        super().__init__(api_key=None)
        self.binary = binary or os.getenv("PIXELRAG_BIN") or _DEFAULT_BINARY

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    async def _fetch(self, url: str, opts: ScrapeOptions) -> ScrapeResult:
        extra = strip_extra_prefix(opts.extra, "pixelrag_")
        output_dir = _output_dir(extra)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _build_command(self.binary, url, opts, output_dir)
        stdout, stderr = await _run_pixelshot(command, opts.timeout_s)

        tile_dirs = _discover_tile_dirs(output_dir, stdout)
        if not tile_dirs:
            raise ValueError(f"pixelshot produced no tile manifests under {output_dir}")

        payload = _capture_payload(url, output_dir.resolve(), tile_dirs)
        reader = extra.get("reader")
        if callable(reader):
            payload = await _read_tiles_with_reader(
                payload,
                cast(LLMCallable, reader),
                prompt=str(extra.get("reader_prompt", _DEFAULT_READER_PROMPT)),
            )
            text, markdown = _text_from_reader_payload(payload)
        else:
            text, markdown = _text_from_payload(payload)

        return ScrapeResult(
            url=url,
            text=text,
            markdown=markdown,
            html=None,
            json=payload,
            engine=self.NAME,
            elapsed_ms=0,
            cost_usd=0.0,
            screenshot_b64=_screenshot_b64(payload) if opts.take_screenshot else None,
            meta={
                "output_dir": str(output_dir.resolve()),
                "tile_count": payload["tile_count"],
                "stdout": stdout,
                "stderr": stderr,
            },
        )


__all__ = [
    "PixelRagEngine",
    "_build_command",
    "_capture_payload",
    "_discover_tile_dirs",
    "_read_tiles_with_reader",
]
