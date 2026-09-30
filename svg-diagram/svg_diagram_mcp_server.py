#!/usr/bin/env python3
"""Local CPU tools for compact, editable SVG model diagram workflows."""

from __future__ import annotations

import asyncio
import base64
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from svg_diagram.models import Diagram, Label
from svg_diagram.renderer import render
from svg_diagram import service
from svg_diagram.artifacts import Detail, Preview, image_bytes, response_text
from svg_diagram.editing import Change, update
from svg_diagram.templates import TemplateName, TemplateLayout, create

mcp = FastMCP(
    name="SVG Diagram",
    json_response=True,
    instructions=(
        "Use create_diagram to draw from a parameterized template in one call. "
        "Use update_diagram with ID-based changes and the returned revision for edits. "
        "These tools return compact findings and an inline preview by default. "
        "Request diagram_catalog(template=...) for that template's parameter schema. "
        "Use render_diagram for custom specs; detail=summary avoids full coordinates. "
        "ELK is an optional automatic layout; explicit pins/waypoints need a native layout. "
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
EDIT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


async def execute(operation, *args, **kwargs) -> dict:
    try:
        return await asyncio.to_thread(operation, *args, **kwargs)
    except Exception as error:
        return {
            "status": "error",
            "error": {"type": type(error).__name__, "message": str(error)[:1800]},
        }


async def artifact_result(result: dict, preview: Preview) -> CallToolResult:
    content = [TextContent(type="text", text=response_text(result))]
    if preview == "inline" and result.get("preview_path"):
        data = await asyncio.to_thread(image_bytes, result["preview_path"])
        content.append(
            ImageContent(
                type="image", data=base64.b64encode(data).decode(), mimeType="image/png"
            )
        )
    return CallToolResult(
        content=content,
        structuredContent=result,
        isError=result.get("status") == "error",
    )


@mcp.tool(annotations=READ_ONLY)
async def diagram_catalog(
    example: Literal["cvae", "stylegan2", "vq-vae"] | None = None,
    include_schema: bool = False,
    template: TemplateName | None = None,
    section: Literal["all", "templates"] = "all",
) -> dict:
    """List semantic themes, fonts, layout modes, and model diagram examples.

    Set template to return only its defaults and optional parameter schema.
    section=templates lists template names without fonts, themes, or full specs.
    Set example for an editable full spec; include_schema adds the diagram schema
    unless template is selected. Does not start a browser or render files.
    """
    return await execute(service.catalog, example, include_schema, template, section)


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
    spec: Diagram,
    output_path: str,
    overwrite: bool = False,
    preview: Preview = "none",
    detail: Detail = "full",
) -> CallToolResult:
    """Render a version-1 diagram spec to an absolute .svg output_path.

    Get a starting spec from diagram_catalog(example=...). Layout can be layered
    (DAG, LR/TB), grid (node row/column), manual (x/y), or elk. ELK accepts ports,
    groups, and edge labels but rejects explicit node pins and edge via points.
    Explicit x/y pins override
    placement. Named ports select sides and fractional offsets. Edge via points
    constrain orthogonal obstacle routing. Groups enclose their member nodes.
    Roles choose semantic colors from dark/print themes; frozen roles are dashed.
    The SVG embeds its spec, editable text, vector math, and stable element IDs.
    Legacy defaults return full coordinates without a PNG. detail=summary reduces
    output; preview=file saves a sibling .preview.png; preview=inline also returns
    an image (at most 1200 pixels per side). Returns a revision for subsequent edits.
    A successful write can
    still have layout warnings: review inspection and preview before delivery.
    Existing outputs require overwrite=true. Nodes<=40, edges<=80; CPU only.
    """
    result = await execute(
        render, spec, output_path, overwrite, preview=preview, detail=detail
    )
    return await artifact_result(result, preview)


@mcp.tool(annotations=WRITE)
async def create_diagram(
    template: TemplateName,
    output_path: str,
    parameters: dict | None = None,
    theme: Literal["dark", "print"] = "dark",
    layout: TemplateLayout = "grid",
    preview: Preview = "inline",
    detail: Detail = "summary",
    overwrite: bool = False,
) -> CallToolResult:
    """Create an editable diagram, inspect it, and preview it in one call.

    Templates: residual (channels/layers/kernel_size), encoder-decoder
    (channels/latent_dim/skip_connections), attention (model_dim/heads/tokens),
    loss-branches (terms with label/symbol/optional formula), tensor-stack
    (shapes/operations), and cvae/stylegan2/vq-vae (existing examples).
    All accept language=en|zh and optional title. Request a template's catalog
    schema when needed. Defaults avoid full specs and coordinate output. Save
    the returned revision and node_ids for update_diagram. Existing artifacts
    require overwrite=true. Preview mode none/file/inline controls saved PNGs and
    image return; layout=elk requests automatic port-aware compound layout.
    """
    result = await execute(
        create,
        template,
        output_path,
        parameters,
        theme,
        layout,
        preview,
        detail,
        overwrite,
    )
    return await artifact_result(result, preview)


@mcp.tool(annotations=EDIT)
async def update_diagram(
    file_path: str,
    changes: list[Change],
    output_path: str = "",
    expected_revision: str | None = None,
    preview: Preview = "inline",
    detail: Detail = "summary",
    overwrite: bool = False,
) -> CallToolResult:
    """Apply 1-100 ID-based spec edits, validate once, then render and preview.

    By default replaces file_path and its generated preview; output_path writes
    a copy (existing copies need overwrite=true). expected_revision prevents
    applying edits to a stale version; changes during rendering also abort.
    Example: changes=[{op:'set_label',id:'input',values:{text:'New input'}},
    {op:'set_node',id:'output',values:{role:'loss'}}]. set_label accepts an optional
    label_index; new text/latex/spans replaces the old content but keeps typography.
    add/set/remove_node/edge/group operate on IDs; values replace named properties.
    set_diagram changes title/description/theme/font_family/layout (layout merges).
    Removals do not cascade: remove associated references in the same batch.
    The embedded spec is the source of truth. Manual SVG-only changes are not
    imported; automatic layouts may move other nodes when content changes.
    """
    result = await execute(
        update,
        file_path,
        changes,
        output_path,
        expected_revision,
        preview,
        detail,
        overwrite,
    )
    return await artifact_result(result, preview)


@mcp.tool(annotations=READ_ONLY)
async def inspect_diagram(file_path: str, include_spec: bool = False) -> dict:
    """Inspect actual SVG geometry, glyph coverage, and internal references.

    Reports overflow, overlapping nodes/labels, edges crossing nodes/labels,
    group membership overlap, duplicate IDs, missing glyph references, and
    missing fonts. Reads the final DOM, not embedded layout coordinates.
    Arbitrary static SVGs receive bounds/font/reference checks; node/edge checks
    require the generated diagram classes. Scripts, external resources, CSS
    imports, foreignObject, and unsupported active SVG elements are rejected.
    Returns a revision; include_spec=true also returns the canonical editable spec.
    Does not assess model correctness or replace a final PNG visual review.
    """
    return await execute(service.inspect_file, file_path, include_spec)


if __name__ == "__main__":
    mcp.run(transport="stdio")
