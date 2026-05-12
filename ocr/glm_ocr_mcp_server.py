"""
GLM-OCR MCP Server — 让 OpenCode agent 直接调用 GLM-OCR 文档解析能力

工具列表:
    ocr_glm        — 解析文档 (图片/PDF)，返回 Markdown 结构化结果
    ocr_glm_status — 查看 OCR 服务状态

自动唤醒逻辑:
    调用 ocr_glm 时，MCP server 会先检查 OCR 服务是否在线:
    - 在线 → 直接解析
    - 离线 → 自动执行 ocr/glm_ocr_start.sh 启动服务，等待就绪后解析

用法 (opencode.jsonc):
    "glm_ocr": {
      "command": "<YOUR-PYTHON>",
      "args": ["<REPO-DIR>/ocr/glm_ocr_mcp_server.py"],
      "enabled": true
    }
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
REPO_DIR = Path(__file__).resolve().parent.parent
START_SCRIPT = REPO_DIR / "ocr" / "glm_ocr_start.sh"
OCR_HOST = os.environ.get("OCR_HOST", "localhost")
OCR_PORT = int(os.environ.get("OCR_PORT", "8002"))

# ---------------------------------------------------------------------------
# MCP Server 实例
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="GLM-OCR",
    json_response=True,
    instructions="Document OCR via GLM-OCR (0.9B VLM). "
                  "Parses images and PDFs to structured Markdown/JSON. "
                  "Supports Chinese, English, formulas (LaTeX), tables, handwriting. "
                  "The OCR server is auto-started on first use.",
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _health_url() -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/health"


def _parse_url() -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/parse"


def _check_ocr_health(timeout: float = 3.0) -> bool:
    """快速检查 OCR 服务是否在线"""
    try:
        req = urllib.request.Request(_health_url())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_ocr_server() -> bool:
    """后台启动 OCR 服务，轮询等待就绪 (最长 90s)"""
    if not START_SCRIPT.exists():
        sys.stderr.write(f"[glm_ocr_mcp] Start script not found: {START_SCRIPT}\n")
        return False

    sys.stderr.write(f"[glm_ocr_mcp] Starting OCR server: {START_SCRIPT}\n")
    subprocess.Popen(
        ["bash", str(START_SCRIPT), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    max_wait = 90
    for i in range(max_wait):
        time.sleep(2)
        if _check_ocr_health(timeout=2.0):
            sys.stderr.write(
                f"[glm_ocr_mcp] OCR server ready after {(i + 1) * 2}s\n"
            )
            return True
    sys.stderr.write("[glm_ocr_mcp] OCR server startup timed out\n")
    return False


def _stop_competing_servers():
    """Stop other GPU-hungry model servers before starting OCR.

    On a 12 GB GPU, only one model can fit at a time.  Kill the
    vision (llama-server) and ASR servers to free VRAM, then pause
    briefly for the GPU driver to reclaim the memory.
    """
    competing = [
        REPO_DIR / "vl" / "llama_start.sh",
        REPO_DIR / "asr" / "qwen3_asr_start.sh",
    ]
    for script in competing:
        if script.exists():
            subprocess.run(
                ["bash", str(script), "stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
    time.sleep(1)  # brief wait for GPU memory reclamation


def _ensure_ocr_ready() -> bool:
    """确保 OCR 服务在线：先检查，不在线则自动启动（启动前释放竞争 GPU）"""
    if _check_ocr_health():
        return True
    _stop_competing_servers()
    sys.stderr.write("[glm_ocr_mcp] OCR server not running, auto-starting...\n")
    return _start_ocr_server()


def _parse_file(
    file_path: str,
    output_format: str = "markdown",
    timeout: int = 180,
) -> dict:
    """调用 OCR REST API 解析文档"""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a regular file: {file_path}"}

    suffix = path.suffix.lower()
    supported = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".pdf", ".webp"}
    if suffix not in supported:
        return {
            "error": f"Unsupported file type: {suffix}. "
                     f"Supported: {', '.join(sorted(supported))}"
        }

    with open(path, "rb") as f:
        file_data = f.read()

    boundary = "----GLMOCRMCPBoundary"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        .encode()
    )
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += file_data
    body += b"\r\n"
    if output_format:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="output_format"\r\n\r\n'.encode()
        body += f"{output_format}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        _parse_url(),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            if "text/plain" in content_type:
                return {"markdown": raw.decode("utf-8", errors="replace")}

            result = json.loads(raw.decode())
    except Exception as e:
        return {"error": f"OCR API call failed: {e}"}

    return result


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def ocr_glm(
    file_path: str,
    output_format: str = "markdown",
) -> dict:
    """Parse a document (image or PDF) using GLM-OCR.

    Extracts text, formulas (as LaTeX), and tables from documents.
    Supports Chinese and English, printed and handwritten text.

    The OCR server is automatically started if not running (first call may
    take 1-2 minutes while the model loads).

    Args:
        file_path: Absolute path to the document file.
                   Supported formats: PNG, JPG, BMP, TIFF, WEBP, PDF.
        output_format: "markdown" (default, returns clean Markdown) or
                       "json" (full structured output with per-page details).

    Returns:
        A dict with keys:
          - markdown: Full markdown text with LaTeX formulas and tables
          - page_count: Number of pages processed
          - pages: Per-page details (when output_format="json")
          - error: Error message if parsing failed

    Example:
        ocr_glm("/home/user/report.pdf")
        ocr_glm("/home/user/photo.png", output_format="json")
    """
    if not _ensure_ocr_ready():
        return {
            "error": "Failed to start OCR server. "
                     "Check logs at /tmp/glm-ocr-server.log"
        }

    result = _parse_file(file_path, output_format=output_format)

    if output_format == "markdown" and "markdown" in result:
        result["_note"] = (
            "Formulas are in LaTeX format (e.g., $E=mc^2$ for inline, "
            "$$...$$ for display). Tables are in Markdown table format."
        )

    return result


@mcp.tool()
def ocr_glm_status() -> dict:
    """Check the status of the GLM-OCR server.

    Returns server health info including GPU memory usage and loaded model.
    """
    if not _check_ocr_health(timeout=2.0):
        return {
            "status": "offline",
            "message": "GLM-OCR server is not running. "
                       "Call ocr_glm() to auto-start it."
        }

    try:
        req = urllib.request.Request(_health_url())
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            info = json.loads(resp.read().decode())
        return info
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
