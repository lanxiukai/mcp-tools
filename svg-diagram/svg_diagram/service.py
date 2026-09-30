"""Public operations shared by the MCP adapter and focused tests."""

from __future__ import annotations

from importlib.metadata import version
import json
from pathlib import Path
import subprocess

from .inspection import inspect_markup, safe_svg
from .labels import measure, public_measurement
from .models import Diagram, Label
from .runtime import (
    MAX_SVG_BYTES,
    absolute_path,
    browser_page,
    element,
    font_info,
    math_batch,
    math_fragment,
    publish,
    svg_bytes,
)
from .themes import THEMES

COMPONENT = Path(__file__).resolve().parents[1]
EXAMPLES = ("cvae", "stylegan2", "vq-vae")


def catalog(example: str | None = None, include_schema: bool = False) -> dict:
    fonts = []
    for family in (
        "Noto Sans CJK SC",
        "Noto Serif CJK SC",
        "DejaVu Sans",
        "DejaVu Sans Mono",
    ):
        try:
            record = font_info(family)
            fonts.append({"family": family, "available": True, "file": record["file"]})
        except (RuntimeError, ValueError):
            fonts.append({"family": family, "available": False})
    try:
        node = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        node = None
    result = {
        "status": "success",
        "spec_version": 1,
        "themes": THEMES,
        "fonts": fonts,
        "layouts": ["layered", "grid", "manual"],
        "directions": ["LR", "TB"],
        "shapes": ["box", "circle"],
        "ports": ["left", "right", "top", "bottom", "custom named ports"],
        "examples": list(EXAMPLES),
        "runtime": {
            "node": node,
            "playwright": version("playwright"),
            "python_mcp": version("mcp"),
        },
        "workflow": "Request an example or include_schema, edit its spec, render_diagram, inspect findings, then preview with format_conversion.svg_to_png. Keep the spec for subsequent edits.",
        "limits": {
            "nodes": 40,
            "edges": 80,
            "math_batch": 128,
            "canvas_side": 8192,
            "canvas_pixels": 32000000,
        },
    }
    if example is not None:
        if example not in EXAMPLES:
            raise ValueError(f"Choose an example from {EXAMPLES}")
        result["spec"] = json.loads(
            (COMPONENT / "examples" / f"{example}.json").read_text()
        )
    if include_schema:
        result["schema"] = Diagram.model_json_schema()
    return result


def render_math_files(
    expressions: list[str],
    output_dir: str,
    font_size: float = 24,
    color: str = "#E7EDF5",
    overwrite: bool = False,
) -> dict:
    # Reuse the label's public validation for size, color, and input bounds.
    for expression in expressions:
        Label(latex=expression, font_size=font_size, color=color)
    if not expressions or len(expressions) > 128:
        raise ValueError("Supply 1-128 formulas")
    directory = absolute_path(output_dir)
    paths = [
        directory / f"formula-{index + 1:03}.svg" for index in range(len(expressions))
    ]
    for path in paths:
        absolute_path(str(path), ".svg")
        if path.exists() and not overwrite:
            raise FileExistsError(f"Output exists; set overwrite=true: {path}")
    outputs = math_batch(expressions)
    records, artifacts = [], []
    for index, (latex, markup) in enumerate(zip(expressions, outputs, strict=True)):
        root, box = math_fragment(markup, font_size, color, f"formula-{index + 1}")
        element("title", parent=root).text = latex
        element("metadata", parent=root).text = json.dumps(
            {"latex": latex, "font_size": font_size, "color": color}
        )
        root.set("aria-label", latex)
        content = svg_bytes(root)
        safe_svg(content.decode())
        artifacts.append(content)
        records.append(
            {
                "latex": latex,
                "output_path": str(paths[index]),
                "width_px": box["width"],
                "height_px": box["height"],
                "baseline_px": box["baseline"],
            }
        )
    for path, content in zip(paths, artifacts, strict=True):
        publish(path, content, overwrite)
    return {
        "status": "success",
        "formulas": records,
        "font_size": font_size,
        "color": color,
    }


def measure_labels(labels: list[Label], font_family: str = "Noto Sans CJK SC") -> dict:
    if not 1 <= len(labels) <= 128:
        raise ValueError("Supply 1-128 labels")
    with browser_page() as page:
        measured = measure(labels, font_family, "#E7EDF5", page)
    return {
        "status": "success",
        "font_family": font_family,
        "labels": [public_measurement(item) for item in measured],
    }


def inspect_file(file_path: str) -> dict:
    source = absolute_path(file_path, ".svg")
    if source.stat().st_size > MAX_SVG_BYTES:
        raise ValueError("SVG input exceeds 16 MiB")
    markup = source.read_text(encoding="utf-8")
    safe_svg(markup)
    with browser_page() as page:
        result = inspect_markup(markup, page)
    return {"file_path": str(source), **result}
