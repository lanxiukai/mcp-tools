#!/usr/bin/env python3
"""MCP server for document format conversion tools.

Exposes 3 tools via MCP stdio protocol:
- markdown_to_pdf:  Convert Markdown files to styled PDF
- html_to_pdf:      Convert HTML files to PDF (preserving original styles)
- pdf_to_text:      Extract text from born-digital PDFs (PyMuPDF)
"""

import importlib
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

import converter as _converter_module

PdfEngine = Literal["chromium", "weasyprint"]
PdfTheme = Literal["print", "sepia", "one-dark-pro"]


def _reload_converter() -> None:
    """Reload converter module to pick up hot-edits without server restart."""
    importlib.reload(_converter_module)

mcp = FastMCP(
    name="Format Conversion",
    json_response=True,
    instructions="Document format conversion tools. "
                  "markdown_to_pdf/html_to_pdf: convert documents to PDF. "
                  "pdf_to_text: extract text from born-digital PDFs.",
)


@mcp.tool()
def markdown_to_pdf(
    file_path: str,
    output_path: str = "",
    engine: PdfEngine = "chromium",
    theme: PdfTheme = "print",
) -> dict:
    """Convert a Markdown file (.md) to a styled PDF.

    Uses markdown-it-py for parsing. Supports two rendering backends:
    - engine="chromium" (default): Uses Playwright/Chromium for PDF output.
      Full MathJax SVG support — recommended for math-heavy documents.
    - engine="weasyprint": Lightweight, good for simple documents without math.
    Supports Chinese fonts, tables, code blocks, blockquotes, and page numbers.
    Three color themes are available: white for printing, warm sepia for
    low-glare reading, and a dark theme inspired by One Dark Pro.

    Args:
        file_path:   Absolute path to the .md file.
        output_path: Absolute path for the output .pdf file.
                     If empty, the PDF is saved next to the source with the
                     same stem (e.g. /tmp/report.md → /tmp/report.pdf).
        engine:      Rendering backend — "chromium" (default) or "weasyprint".
        theme:       Color theme — "print" (white, default), "sepia" (warm),
                     or "one-dark-pro" (dark screen-reading theme).
    """
    src = Path(file_path)
    if not output_path:
        output_path = str(src.with_suffix('.pdf'))

    _reload_converter()
    _converter_module.convert_markdown_to_pdf(
        file_path, output_path,
        engine=engine,
        theme=theme,
    )
    out = Path(output_path)
    return {
        "status": "success",
        "output_path": output_path,
        "size_bytes": out.stat().st_size,
        "theme": theme,
    }


@mcp.tool()
def html_to_pdf(
    file_path: str,
    output_path: str = "",
    engine: PdfEngine = "chromium",
) -> dict:
    """Convert an HTML file (.html) to PDF, preserving original styles.

    Supports two rendering engines:

    - ``chromium`` (default): Uses Playwright headless Chromium.  Pixel-identical
      to Chrome Print → Save as PDF.  Supports all modern CSS.
      Requires: ``pip install playwright && playwright install chromium``.
    - ``weasyprint``: Lightweight, good for simple documents.
      Replaces emoji with font-styled spans.  May not match Chrome perfectly
      for display:flex / display:grid layouts.

    Args:
        file_path:   Absolute path to the .html file.
        output_path: Absolute path for the output .pdf file.
                     If empty, derived from the source stem.
        engine:      Rendering backend: ``"chromium"`` or ``"weasyprint"``.
    """
    src = Path(file_path)
    if not output_path:
        output_path = str(src.with_suffix('.pdf'))

    _reload_converter()
    _converter_module.convert_html_to_pdf(file_path, output_path, engine=engine)
    out = Path(output_path)
    return {
        "status": "success",
        "output_path": output_path,
        "size_bytes": out.stat().st_size,
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
