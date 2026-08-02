"""
Qwen3-ASR API Server (FastAPI)

Usage:
    conda activate qwen-asr
    python asr/qwen3_asr_server.py

API Endpoints:
    GET  /health                            — Health check
    POST /v1/audio/transcriptions           — OpenAI-compatible speech-to-text endpoint
    GET  /v1/models                         — Model list
    GET  /docs                              — Auto-generated API docs

Example calls:
    curl -F "file=@audio.wav" http://localhost:8000/v1/audio/transcriptions
    curl -F "file=@audio.wav" -F "response_format=verbose_json" http://localhost:8000/v1/audio/transcriptions
"""

import argparse
import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Optional

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

if __package__:  # package-mode (import asr.qwen3_asr_server)
    from .model_source import resolve_model_source
else:            # direct-script mode (python asr/qwen3_asr_server.py)
    from model_source import resolve_model_source

# Max audio seconds per model call — keep VRAM within 12 GB budget
_MAX_CHUNK_SEC = 480  # 8 minutes per chunk — balance VRAM safety & speed
REPO_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qwen3-asr-server")


# ---------------------------------------------------------------------------
# Model holder (module-level singleton)
# ---------------------------------------------------------------------------
class ASRModel:
    """Thread-safe ASR model wrapper"""

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
            max_inference_batch_size=1,
            max_new_tokens=4096,
        )
        elapsed = time.time() - t0
        logger.info("Model loaded in %.1fs", elapsed)

    def transcribe(self, audio_path: str, language: Optional[str] = None):
        """Transcribe audio, splitting long files into VRAM-safe chunks."""
        assert self.model is not None, "Model not loaded"

        # read audio and check duration
        data, sr = sf.read(audio_path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)  # mix to mono
        total_sec = len(data) / sr

        if total_sec <= _MAX_CHUNK_SEC:
            # short audio — direct call
            return self.model.transcribe(audio=audio_path, language=language)

        # long audio — chunk it
        chunk_samples = int(_MAX_CHUNK_SEC * sr)
        num_chunks = (len(data) + chunk_samples - 1) // chunk_samples
        logger.info(
            "Long audio detected (%.0fs) — splitting into %d chunks", total_sec, num_chunks,
        )

        tmpdir = tempfile.mkdtemp(prefix="asr_server_chunks_")
        all_text: list[str] = []
        detected_lang = ""

        try:
            for i in range(num_chunks):
                start = i * chunk_samples
                end = min(start + chunk_samples, len(data))
                chunk_path = os.path.join(tmpdir, f"chunk_{i:04d}.wav")
                sf.write(chunk_path, data[start:end], sr, subtype="PCM_16")

                logger.info("Chunk %d/%d (%.0f–%.0fs) ...", i + 1, num_chunks,
                             start / sr, end / sr)
                results = self.model.transcribe(audio=chunk_path, language=language)
                for r in results:
                    all_text.append(r.text if hasattr(r, "text") else r.get("text", ""))
                    if not detected_lang:
                        detected_lang = r.language if hasattr(r, "language") else r.get("language", "")
        finally:
            # clean up temp chunk files
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Combine into a single result matching the original return shape
        combined_text = " ".join(all_text)
        # build a simple result object that looks like the original
        from dataclasses import dataclass
        @dataclass
        class _CombinedResult:
            text: str
            language: str
        return [_CombinedResult(text=combined_text, language=detected_lang)]


asr_model = ASRModel()

# Idle timeout config (seconds): auto-exit to release GPU when no requests for this duration
IDLE_TIMEOUT = int(os.environ.get("ASR_IDLE_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: load model on startup, release GPU memory on shutdown"""
    # Startup
    model_id = resolve_model_source(getattr(app.state, "model_id", None), REPO_DIR)
    device = getattr(app.state, "device", "cuda:0")
    dtype = getattr(app.state, "dtype", "bfloat16")
    try:
        asr_model.load(model_id, device=device, dtype=dtype)
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        sys.exit(1)

    # Initialize active request count & last request time & lock
    app.state._lock = threading.Lock()
    app.state.active_requests = 0
    app.state.last_request_time = time.time()

    # Start idle monitor thread: no active requests + idle timeout → send SIGTERM for graceful shutdown
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
                return  # won't reach here after os.kill, but kept as defensive guard

    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()

    yield

    # Shutdown: release GPU memory
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
# Middleware: track active requests & last request completion time (for idle_monitor)
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
# Pydantic models
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
# Helper functions
# ---------------------------------------------------------------------------
async def save_upload(upload: UploadFile) -> Path:
    """Save uploaded file to a temp file, return the path"""
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
# API Endpoints
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
    file: UploadFile = File(..., description="Audio file (WAV, MP3, FLAC, OGG, etc.)"),
    language: Optional[str] = Form(None, description="Language code (optional, auto-detect if empty, e.g. 'en', 'zh', 'ja')"),
    response_format: ResponseFormat = Form(ResponseFormat.json, description="Response format"),
):
    """
    OpenAI-compatible speech-to-text endpoint.

    Upload an audio file, returns the recognized text.
    Supports WAV, MP3, FLAC, OGG, and other common audio formats.

    Example (curl):
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
        # result may be dict or TranscriptionResult object
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
            # A Response object bypasses the endpoint's default
            # TranscriptionResponse filtering, preserving the expanded shape.
            from fastapi.responses import JSONResponse
            return JSONResponse(content={
                "task": "transcribe",
                "language": str(lang) if lang else "unknown",
                "duration": 0.0,
                "text": text,
                "segments": [],
            })
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
# Entry point
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Qwen3-ASR API Server")
    p.add_argument(
        "--model",
        default=None,
        help="Explicit local directory or Hugging Face model ID",
    )
    p.add_argument("--device", default="cuda:0", help="Device (cuda:0, cpu)")
    p.add_argument("--dtype", default="bfloat16", choices=["float16", "bfloat16", "float32"])
    p.add_argument("--host", default="0.0.0.0", help="Bind address")
    p.add_argument("--port", type=int, default=8000, help="Bind port")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # Store in app.state for lifespan use
    app.state.model_id = args.model
    app.state.device = args.device
    app.state.dtype = args.dtype

    logger.info("Starting Qwen3-ASR API Server on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
