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
import os
import subprocess
import sys
import threading
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

# Idle timeout (seconds) — kill llama-server to release GPU when idle
IDLE_TIMEOUT = int(os.environ.get("VISION_IDLE_TIMEOUT", "30"))
_last_activity = time.time()
_monitor_thread = None  # threading.Thread | None

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


def _stop_competing_servers():
    """Stop other GPU-hungry model servers before starting vision.

    On a 12 GB GPU, only one model can fit at a time.  Kill the
    OCR and ASR servers to free VRAM, then pause briefly for the
    GPU driver to reclaim the memory.
    """
    competing = [
        REPO_DIR / "ocr" / "glm_ocr_start.sh",
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


def _note_activity():
    """Record that an MCP tool was just called (resets the idle timer)."""
    global _last_activity
    _last_activity = time.time()


def _ensure_idle_monitor():
    """Spawn a daemon thread that kills llama-server after IDLE_TIMEOUT seconds
    without MCP tool activity.  Only one monitor runs at a time."""
    global _monitor_thread
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return

    def _loop():
        while True:
            time.sleep(5)
            idle_s = time.time() - _last_activity
            if idle_s > IDLE_TIMEOUT:
                if _check_health(timeout=1.0):
                    sys.stderr.write(
                        f"[vision_mcp] Idle {int(idle_s)}s > {IDLE_TIMEOUT}s, "
                        "stopping llama-server to release GPU...\n"
                    )
                    subprocess.run(
                        ["bash", str(START_SCRIPT), "stop"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                break  # server is down, monitor has done its job

    _monitor_thread = threading.Thread(target=_loop, daemon=True)
    _monitor_thread.start()
    sys.stderr.write(
        f"[vision_mcp] Idle monitor started (timeout={IDLE_TIMEOUT}s)\n"
    )


def _ensure_ready() -> bool:
    """确保 llama-server 在线：先检查，不在线则自动启动（启动前释放竞争 GPU）"""
    if _check_health():
        _note_activity()
        _ensure_idle_monitor()
        return True
    _stop_competing_servers()
    sys.stderr.write(
        "[vision_mcp] llama-server not running, auto-starting...\n"
    )
    ok = _start_server()
    if ok:
        _note_activity()
        _ensure_idle_monitor()
    return ok


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
    ...
    """
    _note_activity()
    return _describe_image_core(file_path)


@mcp.tool()
def vision_status() -> dict:
    """Check the status of the llama-server vision backend.

    Returns server health information including online status.
    """
    _note_activity()
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
