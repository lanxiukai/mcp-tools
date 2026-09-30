#!/usr/bin/env python3
"""Five local CPU tools for reusable, editable SVG model diagrams."""

from __future__ import annotations

import asyncio
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from svg_diagram.models import Diagram, Label
from svg_diagram.renderer import render
from svg_diagram import service

mcp = FastMCP(
    name="SVG Diagram",
    json_response=True,
    instructions=(
        "Use this server to author editable SVG model architecture diagrams. "
        "Start with diagram_catalog for themes, fonts, examples, and schema. "
        "Describe nodes, ports, edges, groups, and layout constraints; render_diagram "
        "measures text/math, routes edges, and checks actual SVG geometry. Read its "
        "inspection issues, then preview with format_conversion.svg_to_png. "
        "Preserve the spec for revisions. Use render_math and measure_labels for custom SVG work. "
        "Model semantics and final visual review remain the agent's responsibility. "
        "All computation is local CPU; no API key or GPU is needed. Existing files "
        "are preserved unless overwrite=true. Inspection accepts only static, self-contained SVG."
    ),
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, openWorldHint=False
)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


async def execute(operation, *args, **kwargs) -> dict:
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except Exception as error:
        return {
            "status": "error",
            "error": {"type": type(error).__name__, "message": str(error)[:1800]},
        }


@mcp.tool(annotations=READ_ONLY)
async def diagram_catalog(
    example: Literal["cvae", "stylegan2", "vq-vae"] | None = None,
    include_schema: bool = False,
) -> dict:
    """List semantic themes, fonts, layout modes, and model diagram examples.

    Set example to return an editable complete spec. include_schema returns the
    versioned JSON schema. Does not start a browser or render any files.
    """
    return await execute(service.catalog, example, include_schema)


@mcp.tool(annotations=WRITE)
async def render_math(
    expressions: list[str],
    output_dir: str,
    font_size: float = 24,
    color: str = "#E7EDF5",
    overwrite: bool = False,
) -> dict:
    """Render 1-128 TeX expressions to standalone SVG paths and exact metrics.

    Supply TeX without math delimiters. Uses MathJax base/AMS mathematics,
    local glyph definitions, and inline SVG styles. Returns width_px, height_px,
    and baseline_px measured from the top. The absolute output_dir receives
    formula-001.svg, etc. Invalid formulas fail before files are published.
    Existing files require overwrite=true. Full LaTeX documents/TikZ are unsupported.
    """
    return await execute(
        service.render_math_files, expressions, output_dir, font_size, color, overwrite
    )


@mcp.tool(annotations=READ_ONLY)
async def measure_labels(
    labels: list[Label], font_family: str = "Noto Sans CJK SC"
) -> dict:
    """Measure up to 128 text, LaTeX, or mixed-span labels in local Chromium.

    Each label has exactly one of text, latex, or spans[{kind, content}]. Optional
    font_size, weight (400/700), max_width, and align control typography. Returns
    line widths, ascent/descent, total bounds, and overflow. Math is indivisible
    during wrapping. Missing fonts/glyphs are explicit errors, not silent fallbacks.
    """
    return await execute(service.measure_labels, labels, font_family)


@mcp.tool(annotations=WRITE)
async def render_diagram(
    spec: Diagram, output_path: str, overwrite: bool = False
) -> dict:
    """Render a version-1 diagram spec to an absolute .svg output_path.

    Get a starting spec from diagram_catalog(example=...). Layout can be layered
    (DAG, LR/TB), grid (node row/column), or manual (x/y). Explicit x/y pins override
    placement. Named ports select sides and fractional offsets. Edge via points
    constrain orthogonal obstacle routing. Groups enclose their member nodes.
    Roles choose semantic colors from dark/print themes; frozen roles are dashed.
    The SVG embeds its spec, editable text, vector math, and stable element IDs.
    Returns layout coordinates and inspection findings. A successful write can
    still have layout warnings: review inspection and preview before delivery.
    Existing outputs require overwrite=true. Nodes<=40, edges<=80; CPU only.
    """
    return await execute(render, spec, output_path, overwrite)


@mcp.tool(annotations=READ_ONLY)
async def inspect_diagram(file_path: str) -> dict:
    """Inspect actual SVG geometry, glyph coverage, and internal references.

    Reports overflow, overlapping nodes/labels, edges crossing nodes/labels,
    group membership overlap, duplicate IDs, missing glyph references, and
    missing fonts. Reads the final DOM, not embedded layout coordinates.
    Arbitrary static SVGs receive bounds/font/reference checks; node/edge checks
    require the generated diagram classes. Scripts, external resources, CSS
    imports, foreignObject, and unsupported active SVG elements are rejected.
    Does not assess model correctness or replace a final PNG visual review.
    """
    return await execute(service.inspect_file, file_path)


if __name__ == "__main__":
    mcp.run(transport="stdio")
