"""
Qwen3-ASR API 服务器 (FastAPI)

用法:
    conda activate qwen-asr
    python asr/qwen3_asr_server.py

API 端点:
    GET  /health                            — 健康检查
    POST /v1/audio/transcriptions           — OpenAI 兼容的语音转文字接口
    GET  /v1/models                         — 模型列表
    GET  /docs                              — 自动生成的 API 文档

调用示例:
    curl -F "file=@audio.wav" http://localhost:8000/v1/audio/transcriptions
    curl -F "file=@audio.wav" -F "response_format=verbose_json" http://localhost:8000/v1/audio/transcriptions
"""

import argparse
import logging
import os
import signal
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Optional

import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qwen3-asr-server")


# ---------------------------------------------------------------------------
# 模型持有者（模块级单例）
# ---------------------------------------------------------------------------
class ASRModel:
    """线程安全的 ASR 模型包装器"""

    def __init__(self):
        self.model = None
        self.model_id: str = ""
        self.device: str = "cuda:0"
        self.dtype: torch.dtype = torch.bfloat16

    def load(self, model_id: str, device: str = "cuda:0", dtype: str = "bfloat16"):
        from qwen_asr import Qwen3ASRModel

        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }

        self.model_id = model_id
        self.device = device
        self.dtype = dtype_map.get(dtype, torch.bfloat16)

        logger.info("Loading model: %s (dtype=%s, device=%s)", model_id, dtype, device)
        t0 = time.time()
        self.model = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=self.dtype,
            device_map=device,
            max_inference_batch_size=8,
            max_new_tokens=4096,
        )
        elapsed = time.time() - t0
        logger.info("Model loaded in %.1fs", elapsed)

    def transcribe(self, audio_path: str, language: Optional[str] = None):
        assert self.model is not None, "Model not loaded"
        results = self.model.transcribe(audio=audio_path, language=language)
        return results


asr_model = ASRModel()

# 空闲超时配置（秒）：无请求超过此时间自动退出释放 GPU
IDLE_TIMEOUT = int(os.environ.get("ASR_IDLE_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时加载模型，关闭时释放 GPU 显存"""
    # 启动
    model_id = getattr(app.state, "model_id", "Qwen/Qwen3-ASR-1.7B")
    device = getattr(app.state, "device", "cuda:0")
    dtype = getattr(app.state, "dtype", "bfloat16")
    try:
        asr_model.load(model_id, device=device, dtype=dtype)
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        sys.exit(1)

    # 初始化活跃请求计数 & 最后请求时间 & 锁
    app.state._lock = threading.Lock()
    app.state.active_requests = 0
    app.state.last_request_time = time.time()

    # 启动空闲监控线程：无活跃请求 + 空闲超时 → 发 SIGTERM 优雅退出
    def idle_monitor():
        while True:
            time.sleep(5)
            with app.state._lock:
                idle_s = time.time() - app.state.last_request_time
                busy = app.state.active_requests > 0
            if not busy and idle_s > IDLE_TIMEOUT:
                logger.info(
                    "Idle timeout reached (%ds > %ds), shutting down to release GPU...",
                    int(idle_s), IDLE_TIMEOUT,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return  # os.kill 后不会执行到这里，但保留防御

    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()

    yield

    # 关闭：释放 GPU 显存
    asr_model.model = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Qwen3-ASR API",
    description="Speech-to-text API powered by Qwen3-ASR-1.7B (OpenAI-compatible)",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware：跟踪活跃请求 & 最后请求完成时间（供 idle_monitor 使用）
# ---------------------------------------------------------------------------
@app.middleware("http")
async def track_activity(request: Request, call_next):
    with app.state._lock:
        app.state.active_requests += 1
    try:
        response = await call_next(request)
        return response
    finally:
        with app.state._lock:
            app.state.active_requests -= 1
            app.state.last_request_time = time.time()


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------
class ResponseFormat(str, Enum):
    json = "json"
    text = "text"
    verbose_json = "verbose_json"


class TranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


class VerboseTranscriptionResponse(BaseModel):
    task: str = "transcribe"
    language: str
    duration: float
    text: str
    segments: list = []


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "qwen"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def save_upload(upload: UploadFile) -> Path:
    """将上传文件保存到临时文件，返回路径"""
    suffix = Path(upload.filename or "audio.wav").suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        while chunk := await upload.read(1024 * 1024):  # 1 MB chunks
            tmp.write(chunk)
        tmp.close()
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "model": asr_model.model_id,
        "device": asr_model.device,
        "dtype": str(asr_model.dtype),
        "gpu_info": {
            "name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A",
            "memory_total_gb": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1) if torch.cuda.is_available() else 0,
            "memory_allocated_gb": round(torch.cuda.memory_allocated(0) / 1024**3, 2) if torch.cuda.is_available() else 0,
        },
    }


@app.get("/v1/models", response_model=ModelListResponse)
async def list_models():
    return ModelListResponse(
        data=[ModelInfo(id=asr_model.model_id)]
    )


@app.post("/v1/audio/transcriptions", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="音频文件 (WAV, MP3, FLAC, OGG 等)"),
    language: Optional[str] = Form(None, description="语言代码 (可选，不填则自动检测，如 'en', 'zh', 'ja')"),
    response_format: ResponseFormat = Form(ResponseFormat.json, description="响应格式"),
):
    """
    OpenAI 兼容的语音转文字接口。

    上传音频文件，返回识别出的文本。
    支持 WAV, MP3, FLAC, OGG 等常见音频格式。

    示例 (curl):
        curl -F "file=@audio.wav" http://localhost:8000/v1/audio/transcriptions
        curl -F "file=@audio.wav" -F "response_format=text" http://localhost:8000/v1/audio/transcriptions
    """
    if asr_model.model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    tmp_path = None
    try:
        tmp_path = await save_upload(file)
        logger.info("Transcribing: %s (%s bytes, language=%s)",
                     file.filename, tmp_path.stat().st_size, language)

        t0 = time.time()
        results = asr_model.transcribe(str(tmp_path), language=language)
        elapsed = time.time() - t0

        if not results:
            raise HTTPException(status_code=500, detail="Transcription returned empty result")

        result = results[0]
        # result 可能是 dict 或 TranscriptionResult 对象
        if hasattr(result, "text"):
            text = result.text
            lang = getattr(result, "language", None)
        else:
            text = result.get("text", "")
            lang = result.get("language", None)

        logger.info("Transcription complete (%.2fs): %s...", elapsed, text[:80])

        if response_format == ResponseFormat.text:
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(content=text)
        elif response_format == ResponseFormat.verbose_json:
            return {
                "task": "transcribe",
                "language": str(lang) if lang else "unknown",
                "duration": 0.0,
                "text": text,
                "segments": [],
            }
        else:  # json
            return TranscriptionResponse(
                text=text,
                language=str(lang) if lang else None,
            )

    except Exception as e:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription error: {e}")
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


@app.get("/")
async def root():
    return {
        "service": "Qwen3-ASR API",
        "model": asr_model.model_id,
        "version": "0.1.0",
        "endpoints": {
            "health": "/health",
            "models": "/v1/models",
            "transcription": "POST /v1/audio/transcriptions",
            "docs": "/docs",
        },
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Qwen3-ASR API Server")
    p.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B", help="HuggingFace model ID")
    p.add_argument("--device", default="cuda:0", help="Device (cuda:0, cpu)")
    p.add_argument("--dtype", default="bfloat16", choices=["float16", "bfloat16", "float32"])
    p.add_argument("--host", default="0.0.0.0", help="Bind address")
    p.add_argument("--port", type=int, default=8000, help="Bind port")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # 存入 app.state，供 lifespan 使用
    app.state.model_id = args.model
    app.state.device = args.device
    app.state.dtype = args.dtype

    logger.info("Starting Qwen3-ASR API Server on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
