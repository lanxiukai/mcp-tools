"""
Qwen3-ASR MCP Server — 让 OpenCode agent 直接调用语音转文字能力

工具列表:
    transcribe_audio   — 转写音频文件 (自动唤醒 ASR 服务)
    transcribe_podcast — 播客长音频转写 + 说话人分离
    asr_status         — 查看 ASR 服务状态

自动唤醒逻辑:
    调用 transcribe_audio 时，MCP server 会先检查 ASR 服务是否在线:
    - 在线 → 直接转写
    - 离线 → 自动执行 asr/qwen3_asr_start.sh 启动服务，等待就绪后转写

用法 (opencode.jsonc):
    "qwen3_asr": {
      "command": "<YOUR-PYTHON>",
      "args": ["<REPO-DIR>/asr/asr_mcp_server.py"],
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
START_SCRIPT = REPO_DIR / "asr" / "qwen3_asr_start.sh"
ASR_HOST = os.environ.get("ASR_HOST", "localhost")
ASR_PORT = int(os.environ.get("ASR_PORT", "8000"))

# ---------------------------------------------------------------------------
# MCP Server 实例
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="Qwen3-ASR",
    json_response=True,
    instructions="Speech-to-text transcription via Qwen3-ASR-1.7B. "
                  "Transcribes audio files (WAV, MP3, FLAC, etc.) to text in 52 languages. "
                  "The ASR server is auto-started on first use.",
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _health_url() -> str:
    return f"http://{ASR_HOST}:{ASR_PORT}/health"


def _transcribe_url() -> str:
    return f"http://{ASR_HOST}:{ASR_PORT}/v1/audio/transcriptions"


def _check_asr_health(timeout: float = 3.0) -> bool:
    """快速检查 ASR 服务是否在线"""
    try:
        req = urllib.request.Request(_health_url())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _start_asr_server() -> bool:
    """后台启动 ASR 服务，轮询等待就绪 (最长 60s)"""
    if not START_SCRIPT.exists():
        sys.stderr.write(f"[asr_mcp] Start script not found: {START_SCRIPT}\n")
        return False

    sys.stderr.write(f"[asr_mcp] Starting ASR server: {START_SCRIPT}\n")
    env = os.environ.copy()
    env["ASR_PYTHON"] = sys.executable  # always point to the interpreter we're already using
    subprocess.Popen(
        ["bash", str(START_SCRIPT), "start"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    max_wait = 60
    for i in range(max_wait):
        time.sleep(1)
        if _check_asr_health(timeout=1.0):
            sys.stderr.write(f"[asr_mcp] ASR server ready after {i + 1}s\n")
            return True
    sys.stderr.write("[asr_mcp] ASR server startup timed out\n")
    return False


def _stop_competing_servers():
    """Stop other GPU-hungry model servers before starting ASR.

    On a 12 GB GPU, only one model can fit at a time.  Kill the
    OCR and vision servers to free VRAM, then pause briefly for
    the GPU driver to reclaim the memory.
    """
    competing = [
        REPO_DIR / "ocr" / "glm_ocr_start.sh",
        REPO_DIR / "vl" / "llama_start.sh",
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


def _ensure_asr_ready() -> bool:
    """确保 ASR 服务在线：先检查，不在线则自动启动（启动前释放竞争 GPU）"""
    if _check_asr_health():
        return True
    _stop_competing_servers()
    sys.stderr.write("[asr_mcp] ASR server not running, auto-starting...\n")
    return _start_asr_server()


def _transcribe_file(file_path: str, language: Optional[str] = None, timeout: int = 1800) -> dict:
    """调用 ASR REST API 转写音频文件 (长音频超时 30 分钟)"""
    from urllib.request import Request, urlopen

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a regular file: {file_path}"}

    with open(path, "rb") as f:
        audio_data = f.read()

    boundary = "----Qwen3ASRMCPBoundary"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += audio_data
    body += b"\r\n"
    if language:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="language"\r\n\r\n'.encode()
        body += f"{language}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()

    req = Request(
        _transcribe_url(),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        return {"error": f"API call failed: {e}"}

    return result


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def transcribe_audio(
    file_path: str,
    language: Optional[str] = None,
) -> dict:
    """Transcribe an audio file to text using Qwen3-ASR-1.7B.

    Supports WAV, MP3, FLAC, OGG, and other common audio formats.
    Supports 52 languages including Chinese, English, Japanese, Korean, etc.
    The ASR server is automatically started if not running.

    Args:
        file_path: Absolute path to the audio file (e.g. /home/user/audio.wav)
        language: Optional language code (e.g. 'en', 'zh', 'ja', 'ko').
                  Leave empty for automatic language detection.

    Returns:
        A dict with keys:
          - text: Transcribed text
          - language: Detected or specified language
          - error: Error message if transcription failed
    """
    if not _ensure_asr_ready():
        return {"error": "Failed to start ASR server. Check logs at /tmp/qwen3-asr-server.log"}

    result = _transcribe_file(file_path, language=language)

    return result


@mcp.tool()
def asr_status() -> dict:
    """Check the status of the Qwen3-ASR server.

    Returns server health info including GPU memory usage.
    """
    if not _check_asr_health(timeout=2.0):
        return {"status": "offline", "message": "ASR server is not running"}

    try:
        req = urllib.request.Request(_health_url())
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            info = json.loads(resp.read().decode())
        return info
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Pipeline path — allow importing from the hyphenated asr-pipeline/ directory
# ---------------------------------------------------------------------------
_PIPELINE_DIR = str(REPO_DIR / "asr-pipeline")
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# Do NOT eagerly import — pyannote/torch are heavy.  Import on first use
# inside transcribe_podcast() so that plain transcribe_audio() stays fast.


@mcp.tool()
def transcribe_podcast(
    file_path: str,
    language: Optional[str] = None,
    context: str = "",
    num_speakers: Optional[int] = None,
) -> dict:
    """Transcribe a podcast/long audio with speaker diarization.

    Two-stage pipeline:
      1. ASR transcription via the REST API (fast, auto-chunked).
      2. Speaker diarization via pyannote (requires HF_TOKEN env var;
         skipped if HF_TOKEN is not set).

    Args:
        file_path: Absolute path to the audio file.
        language: Optional language code (e.g. 'en', 'zh').  Auto-detect if empty.
        context: Space-separated domain terms to improve ASR accuracy.
        num_speakers: Optional hint for the maximum number of speakers.

    Returns:
        A dict with keys:
          - text: Full transcribed text
          - language: Detected or specified language
          - duration_sec: Audio duration in seconds
          - num_speakers: Number of detected speakers (0 if diarization skipped)
          - segments: List of speaker segments with start/end times
          - error: Error message if something failed
    """
    # ---- stage 1: ASR via REST API ----
    if not _ensure_asr_ready():
        return {"error": "Failed to start ASR server. Check logs at /tmp/qwen3-asr-server.log"}

    # Preprocess audio to 16kHz WAV so diarization gets the right format
    import preprocess as _pre
    try:
        wav_path = _pre.preprocess_audio(file_path)
        duration = _pre.get_audio_duration(wav_path)
    except Exception as exc:
        return {"error": f"Preprocessing failed: {exc}"}

    # Transcribe via REST API
    sys.stderr.write(f"[asr_mcp] Transcribing via REST API ...\n")
    asr_result = _transcribe_file(file_path, language=language)
    if "error" in asr_result:
        return asr_result

    full_text = asr_result.get("text", "")
    detected_lang = asr_result.get("language", "")

    # ---- stage 2: diarization (optional) ----
    speaker_segments: list[dict] = []
    num_spk = 0

    hf_token = os.environ.get("HF_TOKEN", "")
    if hf_token:
        try:
            import diarize as _diarize_mod
            sys.stderr.write(f"[asr_mcp] Running speaker diarization ...\n")
            speaker_segments = _diarize_mod.run_diarization(
                wav_path,
                hf_token=hf_token,
                num_speakers=num_speakers,
                device="cuda",
            )
            num_spk = len({s["speaker"] for s in speaker_segments})
            sys.stderr.write(
                f"[asr_mcp] Diarization: {len(speaker_segments)} segments, "
                f"{num_spk} speakers\n"
            )
        except Exception as exc:
            sys.stderr.write(f"[asr_mcp] Diarization failed: {exc}\n")
            # Continue without diarization — still return the transcript

    # ---- stage 3: merge ----
    segments: list[dict] = []
    if speaker_segments:
        import merge as _merge_mod
        # No word timestamps from REST API → use segment-level merge
        segments = _merge_mod.merge_diarization_asr(speaker_segments, [])

    return {
        "text": full_text,
        "language": detected_lang or "unknown",
        "duration_sec": duration,
        "num_speakers": num_spk,
        "segments": [
            {
                "speaker": s["speaker"],
                "start": s["start"],
                "end": s["end"],
            }
            for s in segments
        ],
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
