"""
QwenVision MCP Server — 让 OpenCode agent 通过 Qwen3.6-35B 获取图片描述

工具列表:
    describe_image — 传入图片路径，返回英文描述
    vision_status  — 查看 llama-server 服务状态

自动唤醒逻辑:
    调用 describe_image 时，MCP server 会先检查 llama-server 是否在线:
    - 在线 → 直接调用
    - 离线 → 自动执行 vl/llama_start.sh server 启动服务，等待就绪后调用

用法 (opencode.jsonc):
    "qwen_vision": {
      "command": "<YOUR-PYTHON>",
      "args": ["<REPO-DIR>/vl/vision_mcp_server.py"],
      "enabled": true
    }
"""

import base64
import json
import mimetypes
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------
VISION_HOST = "localhost"
VISION_PORT = 8080
REPO_DIR = Path(__file__).resolve().parent.parent
START_SCRIPT = Path(__file__).resolve().parent / "llama_start.sh"

# ---------------------------------------------------------------------------
# MCP Server 实例
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="QwenVision",
    json_response=True,
    instructions="Image description via Qwen3.6-35B-A3B (MoE VLM). "
                  "Provides English descriptions of image content. "
                  "The llama-server is auto-started on first use.",
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _health_url() -> str:
    return f"http://{VISION_HOST}:{VISION_PORT}/health"


def _chat_url() -> str:
    return f"http://{VISION_HOST}:{VISION_PORT}/v1/chat/completions"


def _check_health(timeout: float = 3.0) -> bool:
    """快速检查 llama-server 是否在线"""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(_health_url())
            return resp.status_code == 200
    except Exception:
        return False


def _start_server() -> bool:
    """后台启动 llama-server，轮询等待就绪 (最长 120s)"""
    if not START_SCRIPT.exists():
        sys.stderr.write(
            f"[vision_mcp] Start script not found: {START_SCRIPT}\n"
        )
        return False

    sys.stderr.write(
        f"[vision_mcp] Starting llama-server: bash {START_SCRIPT} restart\n"
    )

    proc = subprocess.Popen(
        ["bash", str(START_SCRIPT), "restart"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    max_wait = 120
    interval = 2
    for i in range(max_wait // interval):
        time.sleep(interval)
        elapsed = (i + 1) * interval

        if proc.poll() is not None and proc.returncode != 0:
            sys.stderr.write(
                f"[vision_mcp] llama-server start script failed "
                f"(exit code: {proc.returncode}) after {elapsed}s\n"
            )
            return False

        if _check_health(timeout=2.0):
            sys.stderr.write(
                f"[vision_mcp] llama-server ready after {elapsed}s\n"
            )
            return True

    if proc.poll() is None:
        sys.stderr.write(
            f"[vision_mcp] llama-server health check timed out ({max_wait}s), "
            f"but start script still running — model may still be loading\n"
        )
    elif proc.returncode == 0:
        sys.stderr.write(
            f"[vision_mcp] llama-server health check timed out ({max_wait}s), "
            f"server is running but model may still be loading\n"
        )
    return False


def _ensure_ready() -> bool:
    """确保 llama-server 在线：先检查，不在线则自动启动"""
    if _check_health():
        return True
    sys.stderr.write(
        "[vision_mcp] llama-server not running, auto-starting...\n"
    )
    return _start_server()


def _describe_image_core(file_path: str) -> dict:
    """核心逻辑：编码图片并调用 llama-server 的 OpenAI Vision API"""
    path = Path(file_path)

    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a regular file: {file_path}"}

    if not _ensure_ready():
        return {
            "error": (
                "Failed to start llama-server. "
                "Check logs at ~/.llama/llama-server.log "
                "or run 'bash vl/llama_start.sh status'"
            )
        }

    try:
        with open(path, "rb") as f:
            image_bytes = f.read()
    except OSError as e:
        return {"error": f"Failed to read file: {e}"}

    b64_data = base64.b64encode(image_bytes).decode("ascii")
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = "application/octet-stream"

    request_body = {
        "model": "local-model",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Describe this image in detail. "
                    "Include all visible objects, people, text, colors, "
                    "and spatial relationships. Output only the description."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}"
                        },
                    }
                ],
            },
        ],
        "max_tokens": 1024,
        "temperature": 0.7,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    sys.stderr.write(
        f"[vision_mcp] Sending image to llama-server "
        f"(MIME: {mime_type}, size: {len(image_bytes)} bytes)\n"
    )

    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                _chat_url(),
                json=request_body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.RequestError as e:
        return {"error": f"llama-server API request failed: {e}"}

    if resp.status_code != 200:
        return {
            "error": (
                f"llama-server returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}"
            )
        }

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse llama-server response: {e}"}

    choices = data.get("choices", [])
    if not choices:
        return {"error": "llama-server returned empty choices"}

    description = choices[0].get("message", {}).get("content", "")
    tokens_used = data.get("usage", {}).get("total_tokens", 0)

    sys.stderr.write(
        f"[vision_mcp] Response received: "
        f"{tokens_used} tokens, {len(description)} chars\n"
    )

    return {
        "description": description,
        "model": "Qwen3.6-35B-A3B",
        "tokens_used": tokens_used,
    }


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def describe_image(file_path: str) -> dict:
    """Get a detailed English description of an image using Qwen3.6-35B-A3B.

    Sends the image to a local llama.cpp server running the Qwen3.6-35B-A3B
    vision-language model (Q4_K_XL quantized) and returns an English text
    description of the image content.

    The llama-server is automatically started if not running. The first call
    may take 1-2 minutes while the 35B MoE model loads into GPU/CPU memory.

    Note: High-resolution images may consume significant context tokens.
    Recommended image size: under 4096x4096 pixels.

    Args:
        file_path: Absolute path to the image file.
                   Supported formats: PNG, JPG, JPEG, GIF, BMP, WEBP.

    Returns:
        A dict with keys:
          - description: English text description of the image
          - model: "Qwen3.6-35B-A3B"
          - tokens_used: Total tokens consumed for this request
          - error: Error message if the request failed
    """
    return _describe_image_core(file_path)


@mcp.tool()
def vision_status() -> dict:
    """Check the status of the llama-server vision backend.

    Returns server health information including online status.
    """
    if not _check_health(timeout=2.0):
        return {
            "status": "offline",
            "message": (
                "llama-server is not running on "
                f"{VISION_HOST}:{VISION_PORT}"
            ),
        }

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(_health_url())
            return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
