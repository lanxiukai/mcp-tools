#!/usr/bin/env python3
"""MCP server for Qwen-3.7-Plus vision analysis via OpenRouter.

Exposes 4 tools via MCP stdio protocol:
- analyze_image:           General image analysis with custom prompt
- extract_text_from_image: OCR-like text extraction from images
- analyze_chart:           Chart/graph/visualization analysis
- analyze_pdf:             PDF analysis (renders pages to images, then calls Qwen)

Intended as a vision supplement for text-only models (e.g., DeepSeek V4).
The main agent calls these tools with a file path, the server sends the
image to Qwen-3.7-Plus via OpenRouter, and returns text that the text-only
model can reason over.

Environment variables:
    OPENROUTER_API_KEY    (required)  OpenRouter API key
    QWEN_VISION_MODEL     (optional)  Model ID, default qwen/qwen3.7-plus
    OPENROUTER_BASE_URL   (optional)  API endpoint, default https://openrouter.ai/api/v1/chat/completions
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 配置 — 全部来自环境变量，不硬编码私密信息
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("QWEN_VISION_MODEL", "qwen/qwen3.7-plus")
BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL",
    "https://openrouter.ai/api/v1/chat/completions",
)
# OpenRouter 要求的 HTTP Referer header，标明调用来源
HTTP_REFERER = os.environ.get(
    "QWEN_VISION_REFERER",
    "https://github.com/lanxiukai/mcp-tools",
)
APP_TITLE = os.environ.get(
    "QWEN_VISION_APP_TITLE",
    "mcp-tools-qwen-vision",
)

SUPPORTED_TYPES: set[str] = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Qwen Vision",
    json_response=True,
    instructions=(
        "Visual analysis via Qwen-3.7-Plus (OpenRouter API). "
        "Accepts image file paths, returns text descriptions. "
        "Use for: screenshot analysis, chart interpretation, "
        "document reading, UI element description, visual Q&A."
    ),
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _validate_image(file_path: str) -> Path:
    """Validate that file_path is a supported image file.

    Raises FileNotFoundError or ValueError on invalid input.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {file_path}")
    if not path.is_file():
        raise ValueError(f"Not a regular file: {file_path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise ValueError(
            f"Unsupported file type: {suffix}. "
            f"Supported: {', '.join(sorted(SUPPORTED_TYPES))}"
        )
    return path


def _encode_image(file_path: str) -> str:
    """Read an image file and return a base64 data URL."""
    path = _validate_image(file_path)
    suffix = path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    mime = mime_map[suffix]
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _call_qwen(
    image_data_url: str,
    prompt: str,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    timeout: int = 120,
) -> dict:
    """Send an image + prompt to Qwen-3.7-Plus via OpenRouter.

    Returns a dict with keys: text, model, usage (or error on failure).
    """
    if not API_KEY:
        return {
            "error": (
                "OPENROUTER_API_KEY is not set. "
                "Export it in your shell or set it in opencode.jsonc env."
            )
        }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url, "detail": "high"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": HTTP_REFERER,
            "X-Title": APP_TITLE,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        return {"error": f"OpenRouter HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"error": f"OpenRouter connection failed: {e.reason}"}
    except Exception as e:
        return {"error": f"API call failed: {e}"}

    if "error" in result:
        return {"error": json.dumps(result["error"])}

    try:
        content: str = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"error": f"Unexpected API response: {json.dumps(result)[:500]}"}

    return {
        "text": content,
        "model": result.get("model", MODEL),
        "usage": result.get("usage", {}),
    }


def _describe_error(exc: Exception) -> dict:
    """Convert a raised exception into the standard error dict."""
    return {"error": str(exc)}


def _render_pdf_pages(file_path: str, max_pages: int, dpi: int) -> list[str]:
    """Render PDF pages to base64 PNG data URLs.

    Returns a list of data URLs, one per page.
    Raises FileNotFoundError, ValueError on bad input.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")
    if not path.is_file():
        raise ValueError(f"Not a regular file: {file_path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {file_path}")

    try:
        import fitz
    except ImportError:
        raise ImportError(
            "pymupdf (fitz) is required for PDF analysis. "
            "Install it: pip install pymupdf"
        )

    doc = fitz.open(file_path)
    try:
        total = len(doc)
        if total == 0:
            raise ValueError("PDF has no pages")

        pages_to_render = min(total, max_pages)
        if total > max_pages:
            sys.stderr.write(
                f"[qwen_vision] PDF has {total} pages, "
                f"only analyzing first {max_pages}\n"
            )

        data_urls: list[str] = []
        for i in range(pages_to_render):
            page = doc[i]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            data_urls.append(f"data:image/png;base64,{b64}")

        return data_urls
    finally:
        doc.close()


def _call_qwen_multi(
    image_data_urls: list[str],
    prompt: str,
    total_pages: int,
    rendered_pages: int,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    timeout: int = 300,
) -> dict:
    """Send multiple images + prompt to Qwen-3.7-Plus.

    Each image is labelled with its page number.
    Returns a dict with keys: text, model, usage, page_count (or error).
    """
    if not API_KEY:
        return {
            "error": (
                "OPENROUTER_API_KEY is not set. "
                "Export it in your shell or set it in opencode.jsonc env."
            )
        }

    content: list[dict] = []
    for i, data_url in enumerate(image_data_urls):
        page_label = f"Page {i + 1}/{total_pages}"
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url, "detail": "high"},
        })
        content.append({
            "type": "text",
            "text": f"[{page_label}]",
        })

    footer = ""
    if total_pages > rendered_pages:
        footer = (
            f"\n\n(Note: Only the first {rendered_pages} of {total_pages} "
            f"pages were rendered. Mention this in your response.)"
        )
    content.append({"type": "text", "text": prompt + footer})

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": HTTP_REFERER,
            "X-Title": APP_TITLE,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        return {"error": f"OpenRouter HTTP {e.code}: {body}"}
    except urllib.error.URLError as e:
        return {"error": f"OpenRouter connection failed: {e.reason}"}
    except Exception as e:
        return {"error": f"API call failed: {e}"}

    if "error" in result:
        return {"error": json.dumps(result["error"])}

    try:
        content: str = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"error": f"Unexpected API response: {json.dumps(result)[:500]}"}

    return {
        "text": content,
        "model": result.get("model", MODEL),
        "usage": result.get("usage", {}),
        "page_count": total_pages,
        "rendered_pages": rendered_pages,
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def analyze_image(
    file_path: str,
    prompt: str = "",
) -> dict:
    """Analyze an image with Qwen-3.7-Plus and return a text description.

    Pass an image file path and optionally a custom instruction.  The image
    is sent to Qwen-3.7-Plus via OpenRouter and the text response is returned
    for the calling (text-only) agent to consume.

    Typical uses:
    - "What does this screenshot show?"
    - "Describe the UI layout and text content."
    - "Is there a bug visible in this error dialog?"
    - "Read all the text from this slide."

    Args:
        file_path: Absolute path to the image file.
                   Supported: PNG, JPG, JPEG, WebP, GIF, BMP.
        prompt:    What to ask about the image.  If empty, uses a general
                   "describe everything in detail" prompt.

    Returns:
        A dict with:
          - text:  The model's analysis as plain text
          - model: The model that responded
          - usage: Token counts (prompt_tokens, completion_tokens, total_tokens)
          - error: Error message if something failed
    """
    if not prompt:
        prompt = (
            "Please describe this image in thorough detail. "
            "Include all visible text, UI elements, layout structure, "
            "colors, charts, data values, and any notable details. "
            "Be precise and specific — do not summarize or skip anything."
        )

    try:
        data_url = _encode_image(file_path)
        return _call_qwen(data_url, prompt)
    except (FileNotFoundError, ValueError) as e:
        return _describe_error(e)


@mcp.tool()
def extract_text_from_image(file_path: str) -> dict:
    """Extract all visible text from an image (OCR-like).

    A convenience wrapper around analyze_image() with a prompt optimized
    for text extraction.  Best for screenshots, scanned documents, slides,
    receipts, and any image where reading text is the primary goal.

    Args:
        file_path: Absolute path to the image file.

    Returns:
        Same dict format as analyze_image(): text, model, usage, error.
        The 'text' field contains the extracted text with original layout
        and structure preserved.
    """
    prompt = (
        "Extract ALL visible text from this image. "
        "Preserve the original layout, line breaks, headings, and structure. "
        "Include headers, labels, numbers, code snippets, and any small or fine print. "
        "If there are tables, format them as markdown tables. "
        "If there are bullet points or numbered lists, preserve them. "
        "Do not summarize, describe, or add commentary — output the raw text only."
    )

    try:
        data_url = _encode_image(file_path)
        return _call_qwen(data_url, prompt)
    except (FileNotFoundError, ValueError) as e:
        return _describe_error(e)


@mcp.tool()
def analyze_chart(file_path: str) -> dict:
    """Analyze a chart, graph, or data visualization.

    Extracts chart type, axis labels, data series, numeric values, trends,
    and key insights from bar charts, line graphs, pie charts, scatter plots,
    heatmaps, and other data visualizations.

    Args:
        file_path: Absolute path to the chart image.

    Returns:
        Same dict format as analyze_image(): text, model, usage, error.
        The 'text' field includes structured analysis: chart type, axes,
        data series with values, trends, and a summary conclusion.
    """
    prompt = (
        "Analyze this chart/graph in detail and provide a structured breakdown:\n\n"
        "1. CHART TYPE: What kind of chart is this (bar, line, pie, scatter, etc.)?\n"
        "2. AXES: Label each axis with its name and units if visible.\n"
        "3. DATA SERIES: For each data series, list its name (from legend) and "
        "its key numeric values. If exact values are visible, report them precisely.\n"
        "4. TRENDS & PATTERNS: Identify trends, outliers, correlations, and notable patterns.\n"
        "5. CONCLUSION: Summarize the main finding in 1-2 sentences.\n\n"
        "Read the legend, axis labels, and data points carefully. "
        "Be precise with numbers — do not approximate unless values are ambiguous."
    )

    try:
        data_url = _encode_image(file_path)
        return _call_qwen(data_url, prompt)
    except (FileNotFoundError, ValueError) as e:
        return _describe_error(e)


@mcp.tool()
def analyze_pdf(
    file_path: str,
    prompt: str = "",
    *,
    max_pages: int = 5,
    dpi: int = 200,
) -> dict:
    """Analyze a PDF with Qwen-3.7-Plus, preserving colors, images, and layout.

    Renders each PDF page to an image at the specified DPI, then sends all
    pages to Qwen-3.7-Plus for visual analysis. Unlike OCR-only tools,
    this preserves the visual fidelity of the document — colors, embedded
    images, charts, diagrams, and spatial layout are all visible to the model.

    Use this when you need to understand the visual content of a PDF:
    - "What colors are used in this report's charts?"
    - "Describe the layout and images on each page."
    - "What does the diagram on page 3 show?"
    - "Summarize this slide deck."

    For text-only extraction without visual context, use ``extract_text_from_image``
    or the ``glm_ocr`` tool instead.

    Args:
        file_path:  Absolute path to the PDF file.
        prompt:     What to ask about the PDF. If empty, uses a general
                    "describe each page" prompt.
        max_pages:  Maximum pages to analyze (default 5). Pages beyond this
                    are skipped to control cost and latency.
        dpi:        Rendering resolution (default 200). Higher DPI = more
                    detail but larger images and more tokens.

    Returns:
        A dict with:
          - text:           The model's analysis
          - model:          The model that responded
          - usage:          Token usage info
          - page_count:     Total pages in the PDF
          - rendered_pages: How many pages were actually analyzed
          - error:          Error message if something failed
    """
    if not prompt:
        prompt = (
            "Analyze this PDF document. For each page, describe:\n"
            "1. The overall layout and structure\n"
            "2. All visible text (headings, body text, labels, numbers)\n"
            "3. Any images, charts, diagrams, or visual elements — describe "
            "what they show\n"
            "4. Colors, branding, and design elements that stand out\n"
            "5. Key information or data conveyed on the page\n\n"
            "Be thorough and precise. Do not skip visual details."
        )

    try:
        data_urls = _render_pdf_pages(file_path, max_pages=max_pages, dpi=dpi)
    except (FileNotFoundError, ValueError, ImportError) as e:
        return _describe_error(e)

    import fitz
    doc = fitz.open(file_path)
    try:
        total_pages = len(doc)
    finally:
        doc.close()

    rendered = len(data_urls)
    return _call_qwen_multi(
        data_urls,
        prompt,
        total_pages=total_pages,
        rendered_pages=rendered,
        timeout=max(300, rendered * 60),
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
