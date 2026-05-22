#!/usr/bin/env python3
"""MCP server for document format conversion tools.

Exposes 3 tools via MCP stdio protocol:
- markdown_to_pdf:  Convert Markdown files to styled PDF
- html_to_pdf:      Convert HTML files to PDF (preserving original styles)
- pdf_to_text:      Extract text from born-digital PDFs (PyMuPDF)
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from converter import convert_markdown_to_pdf, convert_html_to_pdf, convert_pdf_to_text

mcp = FastMCP(
    name="Format Conversion",
    json_response=True,
    instructions="Document format conversion tools. "
                  "markdown_to_pdf/html_to_pdf: convert documents to PDF. "
                  "pdf_to_text: extract text from born-digital PDFs.",
)


@mcp.tool()
def markdown_to_pdf(file_path: str, output_path: str = "") -> dict:
    """Convert a Markdown file (.md) to a styled PDF.

    Uses markdown-it-py for parsing and WeasyPrint for rendering.
    Supports Chinese fonts, tables, code blocks, blockquotes, and page numbers.

    Args:
        file_path:   Absolute path to the .md file.
        output_path: Absolute path for the output .pdf file.
                     If empty, the PDF is saved next to the source with the
                     same stem (e.g. /tmp/report.md → /tmp/report.pdf).
    """
    try:
        src = Path(file_path)
        if not output_path:
            output_path = str(src.with_suffix('.pdf'))

        convert_markdown_to_pdf(file_path, output_path)
        out = Path(output_path)
        return {
            "status": "success",
            "output_path": output_path,
            "size_bytes": out.stat().st_size,
        }
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Conversion failed: {e}"}


@mcp.tool()
def html_to_pdf(file_path: str, output_path: str = "", engine: str = "chromium") -> dict:
    """Convert an HTML file (.html) to PDF, preserving original styles.

    Supports two rendering engines:

    - ``weasyprint`` (default): Lightweight, good for simple documents.
      Replaces emoji with font-styled spans.  May not match Chrome perfectly
      for display:flex / display:grid layouts.
    - ``chromium``: Uses Playwright headless Chromium.  Pixel-identical to
      Chrome Print → Save as PDF.  Supports all modern CSS.
      Requires: ``pip install playwright && playwright install chromium``.

    Args:
        file_path:   Absolute path to the .html file.
        output_path: Absolute path for the output .pdf file.
                     If empty, derived from the source stem.
        engine:      Rendering backend: ``"weasyprint"`` or ``"chromium"``.
    """
    try:
        src = Path(file_path)
        if not output_path:
            output_path = str(src.with_suffix('.pdf'))

        convert_html_to_pdf(file_path, output_path, engine=engine)  # type: ignore[arg-type]
        out = Path(output_path)
        return {
            "status": "success",
            "output_path": output_path,
            "size_bytes": out.stat().st_size,
        }
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Conversion failed: {e}"}


@mcp.tool()
def pdf_to_text(file_path: str) -> dict:
    """Extract plain text from a born-digital PDF using PyMuPDF.

    **born-digital PDF only** (text that can be selected/copied with a mouse).
    Scanned-image PDFs will return an empty string — use the ``glm_ocr`` tool
    for those.

    Args:
        file_path: Absolute path to the .pdf file.
    """
    try:
        import fitz

        text = convert_pdf_to_text(file_path)
        doc = fitz.open(file_path)
        try:
            page_count = len(doc)
        finally:
            doc.close()

        return {
            "text": text,
            "page_count": page_count,
            "size_chars": len(text),
        }
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Extraction failed: {e}"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
