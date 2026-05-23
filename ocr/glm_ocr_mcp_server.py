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


def _try_start_ocr_server() -> bool:
    """Start OCR server in background, return True if already started or start initiated.
    
    Does NOT block waiting for readiness — returns immediately.
    """
    if _check_ocr_health():
        return True
    _stop_competing_servers()
    if not START_SCRIPT.exists():
        sys.stderr.write(f"[glm_ocr_mcp] Start script not found: {START_SCRIPT}\n")
        return False
    sys.stderr.write(f"[glm_ocr_mcp] Starting OCR server in background: {START_SCRIPT}\n")
    subprocess.Popen(
        ["bash", str(START_SCRIPT), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def _wait_for_ocr_ready(max_wait: int = 120) -> bool:
    """Block until OCR server health check passes (max max_wait seconds)."""
    for i in range(max_wait):
        time.sleep(2)
        if _check_ocr_health(timeout=2.0):
            sys.stderr.write(
                f"[glm_ocr_mcp] OCR server ready after {(i + 1) * 2}s\n"
            )
            return True
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
    """非阻塞启动 OCR 服务：离线则后台启动，返回是否已就绪。
    
    如果返回 False，调用方应告诉 agent "server starting, retry in 30s"。
    """
    if _check_ocr_health():
        return True
    _stop_competing_servers()
    if _check_ocr_health():
        return True
    sys.stderr.write("[glm_ocr_mcp] OCR server not running, starting in background...\n")
    _try_start_ocr_server()
    return False  # 不等待，让 agent 过一会重试


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


def _submit_url() -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/submit"


def _job_url(job_id: str) -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/jobs/{job_id}"


def _job_result_url(job_id: str) -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/jobs/{job_id}/result"


def _submit_file(file_path: str) -> dict:
    """提交文件到 OCR 异步任务队列，立即返回 job_id"""
    path = Path(file_path)
    with open(path, "rb") as f:
        file_data = f.read()

    boundary = "----GLMOCRSubmitBoundary"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        .encode()
    )
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += file_data
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        _submit_url(),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": f"Submit failed: {e}"}


def _poll_job(job_id: str, max_wait: int = 3600, poll_interval: int = 3) -> dict:
    """轮询任务状态直到完成，返回结果（最长等待 max_wait 秒）"""
    waited = 0
    last_progress = -1
    while waited < max_wait:
        try:
            req = urllib.request.Request(_job_url(job_id))
            with urllib.request.urlopen(req, timeout=5) as resp:
                job = json.loads(resp.read().decode())
        except Exception as e:
            return {"error": f"Job status check failed: {e}"}

        status = job.get("status", "unknown")
        progress = job.get("progress", {})
        current = progress.get("current", 0)
        total = progress.get("total", 0)

        # 进度日志
        if current != last_progress and total > 0:
            sys.stderr.write(
                f"[glm_ocr_mcp] Job {job_id}: page {current}/{total} "
                f"(elapsed {waited}s)\n"
            )
            last_progress = current

        if status == "completed":
            # 拿结果
            try:
                req2 = urllib.request.Request(_job_result_url(job_id))
                with urllib.request.urlopen(req2, timeout=30) as resp2:
                    content_type = resp2.headers.get("Content-Type", "")
                    raw = resp2.read()
                    if "text/plain" in content_type:
                        return {"markdown": raw.decode("utf-8", errors="replace")}
                    return json.loads(raw.decode())
            except Exception as e:
                return {"error": f"Result fetch failed: {e}"}

        if status == "failed":
            return {"error": job.get("error", "Unknown error")}

        time.sleep(poll_interval)
        waited += poll_interval

    return {"error": f"Job {job_id} timed out after {max_wait}s"}


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def ocr_glm(
    file_path: str,
    output_format: str = "markdown",
    save_markdown: bool = True,
) -> dict:
    """Parse a document (image or PDF) using GLM-OCR.

    Extracts text, formulas (as LaTeX), and tables from documents.
    Supports Chinese and English, printed and handwritten text.

    For multi-page PDFs: automatically uses async submit + poll to avoid
    MCP timeout.  The tool waits for completion and returns the final result.

    The OCR server is automatically started if not running (first call may
    take 1-2 minutes while the model loads).

    Args:
        file_path: Absolute path to the document file.
                   Supported formats: PNG, JPG, BMP, TIFF, WEBP, PDF.
        output_format: "markdown" (default, returns clean Markdown) or
                       "json" (full structured output with per-page details).
        save_markdown: If True (default), also writes a _ocr.md file
                       alongside the source document.

    Returns:
        A dict with keys:
          - markdown: Full markdown text with LaTeX formulas and tables
          - page_count: Number of pages processed
          - pages: Per-page details (when output_format="json")
          - markdown_path: Path to saved .md file (if save_markdown=True)
          - error: Error message if parsing failed

    Example:
        ocr_glm("/home/user/report.pdf")
        ocr_glm("/home/user/photo.png", output_format="json")
    """
    if not _ensure_ocr_ready():
        return {
            "error": "OCR server is auto-starting (model loading, ~30s). "
                     "Please wait 30 seconds and retry the same call — "
                     "the server will be ready then."
        }

    path = Path(file_path)
    suffix = path.suffix.lower()

    # 小文件 / 图片：同步解析（快，不受超时影响）
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}:
        result = _parse_file(file_path, output_format=output_format)
    else:
        # PDF：走异步任务，轮询等待完成
        sys.stderr.write(f"[glm_ocr_mcp] Submitting async OCR job for {path.name}...\n")
        submitted = _submit_file(file_path)
        if "error" in submitted:
            return submitted
        job_id = submitted["job_id"]
        total = submitted.get("total_pages", 0)
        sys.stderr.write(
            f"[glm_ocr_mcp] Job {job_id} submitted ({total} pages), polling...\n"
        )
        result = _poll_job(job_id)
        if "error" in result:
            return result
        result["page_count"] = total

    # Auto-save markdown
    if save_markdown and "markdown" in result and result["markdown"].strip():
        md_path = path.with_suffix(".md")
        # For PDFs, use _ocr suffix to avoid overwriting source
        if suffix == ".pdf":
            md_path = path.with_name(path.stem + "_ocr.md")
        md_path.write_text(result["markdown"], encoding="utf-8")
        result["markdown_path"] = str(md_path)

    if output_format == "markdown" and "markdown" in result:
        result["_note"] = (
            "Formulas are in LaTeX format (e.g., $E=mc^2$ for inline, "
            "$$...$$ for display). Tables are in Markdown table format."
        )

    return result


@mcp.tool()
def ocr_glm_submit(file_path: str) -> dict:
    """Submit a document for async OCR processing, returns immediately with job_id.

    Use ocr_glm_wait(job_id) to poll for results.  This is useful when you
    want to submit multiple documents in parallel and wait for them later.

    Args:
        file_path: Absolute path to the document file.

    Returns:
        A dict with: job_id, status, total_pages, filename.
    """
    if not _ensure_ocr_ready():
        return {
            "error": "OCR server is auto-starting (model loading, ~30s). "
                     "Please wait 30 seconds and retry."
        }
    return _submit_file(file_path)


@mcp.tool()
def ocr_glm_wait(job_id: str, max_wait: int = 1800) -> dict:
    """Poll an async OCR job until completion and return the result.

    Blocks until the job completes or times out.  Reports progress to stderr.

    Args:
        job_id:   The job ID returned by ocr_glm_submit().
        max_wait: Maximum seconds to wait (default 1800 = 30 minutes).

    Returns:
        Same result format as ocr_glm(): markdown, page_count, pages, etc.
    """
    if not _check_ocr_health(timeout=2.0):
        return {
            "error": "OCR server is not running. "
                     "Call ocr_glm() or ocr_glm_submit() to auto-start it."
        }
    return _poll_job(job_id, max_wait=max_wait)


@mcp.tool()
def ocr_glm_status(job_id: str = "") -> dict:
    """Check the status of the GLM-OCR server or a specific job.

    Args:
        job_id: Optional. If provided, returns job status and progress.
                If empty, returns server health info.

    Returns server health info including GPU memory usage and loaded model,
    or job status with current/total progress.
    """
    if job_id:
        try:
            req = urllib.request.Request(_job_url(job_id))
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return {"error": str(e)}

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
