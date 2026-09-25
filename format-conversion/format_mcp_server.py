#!/usr/bin/env python3
"""MCP server for document format conversion tools.

Exposes 6 tools via MCP stdio protocol:
- resolve_pdf_destination: Verify a chapter's actual PDF page and coordinates
- inspect_pdf_links: Check local references and actual derived PDF filenames
- markdown_to_pdf:  Convert Markdown files to styled PDF
- html_to_pdf:      Convert HTML files to themed, print-aware PDF output
- pdf_to_text:      Extract text from born-digital PDFs (PyMuPDF)
- svg_to_png:       Rasterize self-contained SVG files to bounded PNG images
"""

import importlib
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

import converter as _converter_module
import document_links as _links_module
import pdf_destinations as _destinations_module

PdfEngine = Literal["chromium", "weasyprint"]
PdfTheme = Literal["print", "sepia", "one-dark-pro"]


def _reload_converter() -> None:
    """Reload converter module to pick up hot-edits without server restart."""
    importlib.reload(_destinations_module)
    importlib.reload(_links_module)
    importlib.reload(_converter_module)

mcp = FastMCP(
    name="Format Conversion",
    json_response=True,
    instructions="Use this server before ad hoc shell converters for born-digital PDF "
                 "text extraction, Markdown or HTML-to-PDF conversion, and safe SVG-to-PNG "
                 "rasterization. Use pdf_to_text "
                 "before OCR; if it returns empty or inadequate text for a scanned PDF, "
                 "switch to the OCR server. markdown_to_pdf and html_to_pdf create PDFs; "
                 "pdf_to_text extracts embedded text; svg_to_png creates a bounded PNG "
                 "without loading external file or network resources. Before Markdown/HTML "
                 "conversion, call inspect_pdf_links. Review existing PDF filenames and "
                 "multiple editions; pass pdf_targets to select renamed PDFs or null to "
                 "keep a source file. Never assume every Markdown/HTML file has a PDF. "
                 "Non-document local references keep their original file targets. For PDF "
                 "chapter links, call resolve_pdf_destination on the actual target PDF; "
                 "review its physical page and evidence, then pass the returned fragment "
                 "in pdf_destinations keyed by the original href. Never guess page offsets "
                 "or reuse a source anchor without verifying the PDF destination.",
)


@mcp.tool()
def inspect_pdf_links(
    file_path: str,
    pdf_targets: dict[str, str | None] | None = None,
    pdf_destinations: dict[str, str] | None = None,
) -> dict:
    """Inspect Markdown/HTML references before PDF conversion, without writing files.

    Reports local source paths, source existence, actual PDF candidates, nearby
    PDFs and selection evidence. A unique same-stem PDF or recorded source can
    be selected automatically; variants and competing sources need review.
    For differently named legacy PDFs, inspect nearby files and project output
    conventions; the tool cannot infer arbitrary correspondence from a name.
    Supply pdf_targets mapping source filenames to actual PDF filenames, or
    null to retain the source (e.g. README.md without a PDF). Relative mapping
    paths resolve from file_path's directory. A target's .pdf-links.json can
    persist a reviewed mapping, relative to that manifest's directory.
    Non-Markdown/HTML targets such as .py files retain their original paths.
    PDF chapter fragments are verified against the actual PDF. Ambiguous or
    missing chapters require resolve_pdf_destination and a pdf_destinations
    mapping from the original href to the chosen verified fragment.
    """
    _reload_converter()
    return _links_module.inspect_pdf_links(file_path, pdf_targets, pdf_destinations)


@mcp.tool()
def resolve_pdf_destination(file_path: str, target: str, page: int | None = None) -> dict:
    """Locate a chapter in the actual target PDF without writing files.

    Searches existing named destinations, exact normalized outline titles, then
    complete text lines. target is the title, source anchor, or PDF fragment.
    Returns status, physical 1-based page, printed page_label, evidence basis,
    and candidates. Only a unique match returns a top-level fragment and uri.
    For repeated titles, inspect the candidates and use page to narrow the
    search or select a candidate's fragment. Never guess printed-page offsets.
    For scans without usable text or bookmarks, use OCR to establish the page,
    then validate page=N here. Text-line matches require context review.
    Use the returned URI for PDF links, or pass pdf_destinations={original_href:
    fragment} when converting Markdown/HTML. Recheck after the target changes.
    """
    _reload_converter()
    return _destinations_module.resolve_pdf_destination(file_path, target, page)


@mcp.tool()
def markdown_to_pdf(
    file_path: str,
    output_path: str = "",
    engine: PdfEngine = "chromium",
    theme: PdfTheme = "print",
    pdf_targets: dict[str, str | None] | None = None,
    pdf_destinations: dict[str, str] | None = None,
    link_policy: Literal["prefer-pdf", "preserve"] = "prefer-pdf",
) -> dict:
    """Convert a Markdown file (.md) to a styled PDF.

    Uses markdown-it-py for parsing. Supports two rendering backends:
    - engine="chromium" (default): Uses Playwright/Chromium for PDF output.
      Full MathJax SVG support — recommended for math-heavy documents.
    - engine="weasyprint": Lightweight, good for simple documents without math.
    Supports Chinese fonts, tables, code blocks, blockquotes, and page numbers.
    Three color themes are available: white for printing, warm sepia for
    low-glare reading, and a dark theme inspired by One Dark Pro Night Flat.
    Preserves clickable web and local file links, including file:// URLs and
    relative paths into other repositories, resolved from the source directory.
    Call inspect_pdf_links first. Existing unambiguous derived PDFs replace
    local Markdown/HTML targets; absent PDFs leave the source link unchanged.
    Multiple PDF candidates require pdf_targets; other file formats stay intact.

    Args:
        file_path:   Absolute path to the .md file.
        output_path: Absolute path for the output .pdf file.
                     If empty, the PDF is saved next to the source with the
                     same stem (e.g. /tmp/report.md → /tmp/report.pdf).
        engine:      Rendering backend — "chromium" (default) or "weasyprint".
        theme:       Color theme — "print" (white, default), "sepia" (warm),
                     or "one-dark-pro" (One Dark Pro Night Flat-inspired).
        pdf_targets: Reviewed source-to-PDF paths, or null values to keep sources.
                     Relative paths resolve from the source directory.
        pdf_destinations: Original hrefs mapped to fragments from resolve_pdf_destination.
        link_policy: "prefer-pdf" selects existing PDFs; "preserve" keeps sources.
    """
    src = Path(file_path)
    if not output_path:
        output_path = str(src.with_suffix('.pdf'))

    _reload_converter()
    link_report = _converter_module.convert_markdown_to_pdf(
        file_path, output_path,
        engine=engine,
        theme=theme,
        pdf_targets=pdf_targets,
        pdf_destinations=pdf_destinations,
        link_policy=link_policy,
    )
    out = Path(output_path)
    return {
        "status": "success",
        "output_path": output_path,
        "size_bytes": out.stat().st_size,
        "theme": theme,
        "link_report": link_report,
    }


@mcp.tool()
def html_to_pdf(
    file_path: str,
    output_path: str = "",
    engine: PdfEngine = "chromium",
    theme: PdfTheme = "print",
    pdf_targets: dict[str, str | None] | None = None,
    pdf_destinations: dict[str, str] | None = None,
    link_policy: Literal["prefer-pdf", "preserve"] = "prefer-pdf",
) -> dict:
    """Convert an HTML file (.html) to PDF with print-aware styling.

    Supports two rendering engines:

    - ``chromium`` (default): Uses Playwright headless Chromium.  Pixel-identical
      to Chrome Print → Save as PDF.  Supports all modern CSS.
      Requires: ``pip install playwright && playwright install chromium``.
    - ``weasyprint``: Lightweight, good for simple documents.
      Replaces emoji with font-styled spans.  May not match Chrome perfectly
      for display:flex / display:grid layouts.

    Ordinary HTML keeps its original styles. Recognized portable analytics
    reports receive a scoped A4 print profile for typography, cards, charts,
    and source deduplication. Chromium also converts very wide tables to
    labeled record cards. Three color themes are available: white for printing,
    warm sepia for low-glare reading, and One Dark Pro for dark-room reading.
    Preserves clickable web and local file links, including paths into other
    repositories. Relative links respect the source directory or HTML base URL.
    Call inspect_pdf_links first. Local Markdown/HTML links prefer verified
    existing PDFs. Multiple editions require pdf_targets; references without
    PDFs and other formats keep the source file. Only authored static anchors
    are inspected; script-created links need an authored equivalent or review.

    Args:
        file_path:   Absolute path to the .html file.
        output_path: Absolute path for the output .pdf file.
                     If empty, derived from the source stem.
        engine:      Rendering backend: ``"chromium"`` or ``"weasyprint"``.
        theme:       Color theme: ``"print"`` (white, default), ``"sepia"``
                     (warm), or ``"one-dark-pro"`` (dark).
        pdf_targets: Reviewed source-to-PDF paths, or null values to keep sources.
                     Relative paths resolve from the source directory.
        pdf_destinations: Original hrefs mapped to fragments from resolve_pdf_destination.
        link_policy: "prefer-pdf" selects existing PDFs; "preserve" keeps sources.
    """
    src = Path(file_path)
    if not output_path:
        output_path = str(src.with_suffix('.pdf'))

    _reload_converter()
    link_report = _converter_module.convert_html_to_pdf(
        file_path,
        output_path,
        engine=engine,
        theme=theme,
        pdf_targets=pdf_targets,
        pdf_destinations=pdf_destinations,
        link_policy=link_policy,
    )
    out = Path(output_path)
    return {
        "status": "success",
        "output_path": output_path,
        "size_bytes": out.stat().st_size,
        "theme": theme,
        "link_report": link_report,
    }


@mcp.tool()
def svg_to_png(
    file_path: str,
    output_path: str = "",
    scale: float = 1.0,
    output_width: int | None = None,
    output_height: int | None = None,
    background_color: str = "",
) -> dict:
    """Rasterize a self-contained SVG file to a PNG image.

    The conversion runs locally on CPU. External file and network references
    are blocked; embedded ``data:`` resources remain supported. Rendered output
    is limited to 8192 pixels per side and 32 million total pixels, then
    validated and atomically published so a failure cannot replace an existing
    destination with partial bytes.

    Args:
        file_path:       Absolute path to the .svg file.
        output_path:     Absolute path for the output .png file. If empty, the
                         PNG is saved beside the source with the same stem.
        scale:           Positive scale factor up to 16. Cannot be combined
                         with output_width or output_height.
        output_width:    Optional positive pixel width. If output_height is
                         omitted, the aspect ratio is preserved.
        output_height:   Optional positive pixel height. If output_width is
                         omitted, the aspect ratio is preserved.
        background_color: Optional CSS color for otherwise transparent pixels.
    """
    source = Path(file_path)
    if not output_path:
        output_path = str(source.with_suffix(".png"))

    _reload_converter()
    width, height = _converter_module.convert_svg_to_png(
        file_path,
        output_path,
        scale=scale,
        output_width=output_width,
        output_height=output_height,
        background_color=background_color,
    )
    output = Path(output_path)
    return {
        "status": "success",
        "output_path": output_path,
        "size_bytes": output.stat().st_size,
        "width": width,
        "height": height,
        "external_resources": "blocked",
    }


@mcp.tool()
def pdf_to_text(file_path: str, save_text: bool = True) -> dict:
    """Extract plain text from a born-digital PDF using PyMuPDF.

    **born-digital PDF only** (text that can be selected/copied with a mouse).
    Scanned-image PDFs will return an empty string — use ``ocr_document``
    for those.

    Args:
        file_path: Absolute path to the .pdf file.
        save_text: If True (default), also writes a .txt file alongside the PDF.
    """
    import fitz

    _reload_converter()
    text = _converter_module.convert_pdf_to_text(file_path)
    doc = fitz.open(file_path)
    try:
        page_count = len(doc)
    finally:
        doc.close()

    result: dict = {
        "text": text,
        "page_count": page_count,
        "size_chars": len(text),
    }

    if save_text and text.strip():
        txt_path = str(Path(file_path).with_suffix('.txt'))
        Path(txt_path).write_text(text, encoding='utf-8')
        result["text_path"] = txt_path

    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
