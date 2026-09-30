"""Compact responses, bounded previews, and revisions for diagram editing."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Literal

import cairosvg
from PIL import Image

from .inspection import safe_svg
from .models import Diagram
from .runtime import MAX_SVG_BYTES, NS, absolute_path

Preview = Literal["none", "file", "inline"]
Detail = Literal["summary", "full"]


def revision(content: bytes) -> str:
    """An optimistic edit precondition, not an artifact integrity certificate."""
    return hashlib.sha256(content).hexdigest()[:20]


def read_diagram(file_path: str) -> tuple[Path, Diagram, str]:
    path = absolute_path(file_path, ".svg")
    if path.stat().st_size > MAX_SVG_BYTES:
        raise ValueError("SVG input exceeds 16 MiB")
    content = path.read_bytes()
    root = safe_svg(content.decode("utf-8"))
    metadata = root.findall(f"{{{NS}}}metadata[@id='diagram-spec']")
    if len(metadata) != 1 or not metadata[0].text:
        raise ValueError(
            "Editing requires one embedded diagram-spec; create or render a diagram first"
        )
    return path, Diagram.model_validate_json(metadata[0].text), revision(content)


def preview_png(markup: bytes) -> bytes:
    """Rasterize validated generated markup with the existing CairoSVG/Pillow stack."""
    root = safe_svg(markup.decode("utf-8"))
    width, height = round(float(root.get("width"))), round(float(root.get("height")))
    if min(width, height) < 1 or max(width, height) > 8192 or width * height > 32000000:
        raise ValueError("Preview exceeds the bounded PNG canvas")
    # Preserve intrinsic fractional dimensions exactly as Format Conversion does.
    data = cairosvg.svg2png(bytestring=markup)
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.format != "PNG" or image.size != (width, height):
            raise ValueError("PNG preview validation failed")
    return data


def image_bytes(file_path: str, max_side: int = 1200) -> bytes:
    """Bound inline image size; the saved PNG retains its original resolution."""
    with Image.open(file_path) as image:
        image.load()
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
    return stream.getvalue()


def summarize(result: dict, spec: Diagram, detail: Detail) -> dict:
    if detail == "full":
        return result
    if detail != "summary":
        raise ValueError("detail must be summary or full")
    report = result["inspection"]
    output = {
        key: result[key]
        for key in ("status", "output_path", "revision", "preview_path")
        if key in result
    }
    output["layout_mode"] = spec.layout.mode
    output["node_ids"] = [node.id for node in spec.nodes]
    output["inspection"] = {
        "status": report["status"],
        "counts": report["counts"],
        "issues": report["issues"][:12],
        "issues_omitted": report["issues_omitted"] + max(0, len(report["issues"]) - 12),
    }
    return output


def response_text(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
