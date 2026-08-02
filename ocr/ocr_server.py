"""
Generic OCR API Server (FastAPI)

Usage:
    mamba run -n mcp-local-ocr python -m ocr.ocr_server

API Endpoints:
    GET  /health                      — Health check
    POST /v1/ocr/parse                — Document parsing (image/PDF → Markdown/JSON)
    POST /v1/ocr/submit               — Async job submission (PDF → job_id)
    GET  /v1/ocr/jobs/{job_id}        — Job status / progress
    GET  /v1/ocr/jobs/{job_id}/result — Job result
    GET  /v1/models                   — Model list
    GET  /docs                        — Auto-generated API docs

Example calls:
    curl -F "file=@image.png" http://localhost:8002/v1/ocr/parse
    curl -F "file=@image.png" -F "output_format=markdown" http://localhost:8002/v1/ocr/parse
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
from pathlib import Path
from typing import Optional

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

import anyio
import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ocr.job_api import (
    JobResultResponse,
    JobStatusResponse,
    JobSubmissionResponse,
    is_server_busy,
    result_response,
    status_response,
    submission_response,
)
from ocr.job_manifest import JobId
from ocr.job_scheduler import (
    DurableJobScheduler,
    JobFailedError,
    JobNotFoundError,
    JobNotReadyError,
    JobQueueFullError,
    JobSchedulerConfig,
)
from ocr.job_store import JobSourceError
from ocr.model_adapter import OCRModel, default_model_name
from ocr.server_job_support import ModelChunkExecutor, ModelPage, ModelPrediction, assemble_markdown

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ocr-server")

ocr_model = OCRModel()

# ---------------------------------------------------------------------------
# Idle timeout config (seconds): auto-exit to release GPU when no work remains
IDLE_TIMEOUT = int(os.environ.get("OCR_IDLE_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: load model on startup, release GPU memory on shutdown"""
    model_name = getattr(app.state, "model_name", default_model_name())
    device = getattr(app.state, "device", "cuda")

    try:
        ocr_model.load(model_name=model_name, device=device)
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        sys.exit(1)

    scheduler = DurableJobScheduler(
        JobSchedulerConfig.from_environment(),
        ModelChunkExecutor(ocr_model),
    )
    scheduler.start()
    app.state.scheduler = scheduler

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
                active_requests = app.state.active_requests
            if not is_server_busy(active_requests=active_requests, scheduler=scheduler) and idle_s > IDLE_TIMEOUT:
                logger.info(
                    "Idle timeout reached (%ds > %ds), shutting down to release GPU...",
                    int(idle_s), IDLE_TIMEOUT,
                )
                os.kill(os.getpid(), signal.SIGTERM)
                return

    monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
    monitor_thread.start()

    yield

    # Shutdown: release GPU memory
    scheduler.stop()
    ocr_model.model = None
    ocr_model.processor = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="OCR API",
    description=(
        "Model-independent document OCR API. The current backend is PaddleOCR-VL-1.6 "
        "and supports multilingual text, formulas, tables, charts, seals, and handwriting."
    ),
    version="1.0.0",
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
class ParseResponse(BaseModel):
    success: bool
    model: str
    input_path: str
    page_count: int
    pages: list[ModelPage] = []
    markdown: str = ""
    error: Optional[str] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "local-ocr"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
async def save_upload(upload: UploadFile) -> Path:
    """Save uploaded file to a temp file, return the path"""
    suffix = Path(upload.filename or "document.png").suffix or ".png"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    try:
        while chunk := await upload.read(1024 * 1024):  # 1 MB chunks
            tmp.write(chunk)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        tmp.close()
    return tmp_path


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    gpu_info = {}
    if torch.cuda.is_available():
        gpu_info = {
            "name": torch.cuda.get_device_name(0),
            "memory_total_gb": round(
                torch.cuda.get_device_properties(0).total_memory / 1024**3, 1
            ),
            "memory_allocated_gb": round(
                torch.cuda.memory_allocated(0) / 1024**3, 2
            ),
            "memory_reserved_gb": round(
                torch.cuda.memory_reserved(0) / 1024**3, 2
            ),
        }

    return {
        "status": "ok",
        "model": ocr_model.model_name,
        "device": ocr_model.device,
        "backend": {
            "layout_enabled": ocr_model.use_layout,
            "layout_model": ocr_model.layout_model.name,
            "recognition_batch_size": ocr_model.recognition_batch_size,
            "page_batch_size": ocr_model.page_batch_size,
            "kv_cache_enabled": ocr_model.use_kv_cache,
        },
        "gpu_info": gpu_info,
    }


@app.get("/v1/models", response_model=ModelListResponse)
async def list_models():
    return ModelListResponse(
        data=[ModelInfo(id=ocr_model.model_name)]
    )


@app.post("/v1/ocr/parse", response_model=ParseResponse)
async def parse_document(
    file: UploadFile = File(..., description="Image or PDF file (PNG, JPG, PDF, etc.)"),
    output_format: str = Form("json", description="Output format: 'json' or 'markdown'"),
):
    """
    Document parsing endpoint: upload an image or PDF, returns structured OCR results.

    - Supports Chinese, English, formulas (LaTeX), tables
    - Handwriting recognition
    - Multi-page PDF processing (requires pymupdf)

    Example (curl):
        curl -F "file=@image.png" http://localhost:8002/v1/ocr/parse
        curl -F "file=@image.png" -F "output_format=markdown" http://localhost:8002/v1/ocr/parse
    """
    if ocr_model.model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    tmp_path = await save_upload(file)
    try:
        logger.info(
            "Parsing document: %s (%s bytes)",
            file.filename, tmp_path.stat().st_size,
        )
        t0 = time.time()
        scheduler = app.state.scheduler
        completed = await anyio.to_thread.run_sync(scheduler.submit_and_wait, tmp_path)
        artifacts = await anyio.to_thread.run_sync(scheduler.result, completed.job_id)
        markdown = await anyio.to_thread.run_sync(assemble_markdown, artifacts)
        elapsed = time.time() - t0
        logger.info(
            "Parsing complete (%.2fs), %d pages", elapsed, artifacts.page_count
        )
        if output_format == "markdown":
            return PlainTextResponse(
                content=markdown, media_type="text/plain; charset=utf-8"
            )
        return ParseResponse(
            success=True,
            model=ocr_model.model_name,
            input_path=file.filename or "upload",
            page_count=artifacts.page_count,
            pages=[],
            markdown=markdown,
        )
    except JobQueueFullError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except JobSourceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except JobFailedError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"OCR artifact error: {error}") from error
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Async job endpoints ──

@app.post("/v1/ocr/submit", response_model=JobSubmissionResponse)
async def submit_document(
    file: UploadFile = File(..., description="Image or PDF file"),
):
    """Durably submit one document and return its artifact-first queue metadata."""
    if ocr_model.model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    tmp_path = await save_upload(file)
    try:
        scheduler = app.state.scheduler
        submitted = await anyio.to_thread.run_sync(scheduler.submit, tmp_path)
        return submission_response(submitted)
    except JobQueueFullError as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except JobSourceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Submit failed: {error}") from error
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/v1/ocr/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(job_id: str):
    """Query durable job state, progress, and artifact metadata."""
    scheduler = app.state.scheduler
    try:
        snapshot = await anyio.to_thread.run_sync(scheduler.status, JobId(job_id))
    except JobNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return status_response(snapshot)


@app.get("/v1/ocr/jobs/{job_id}/result", response_model=JobResultResponse)
async def job_result(job_id: str):
    """Return completed artifact metadata without embedding Markdown."""
    scheduler = app.state.scheduler
    try:
        artifacts = await anyio.to_thread.run_sync(scheduler.result, JobId(job_id))
    except JobNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except JobFailedError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except JobNotReadyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return result_response(artifacts)

@app.get("/")
async def root():
    return {
        "service": "OCR API",
        "model": ocr_model.model_name,
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "models": "/v1/models",
            "parse": "POST /v1/ocr/parse",
            "docs": "/docs",
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Model-independent OCR API Server")
    p.add_argument(
        "--model",
        default=os.environ.get("OCR_MODEL_NAME", default_model_name()),
        help="Local model directory or Hugging Face model ID",
    )
    p.add_argument("--device", default="cuda", help="Device (cuda, cpu)")
    p.add_argument("--host", default="127.0.0.1", help="Bind address")
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OCR_PORT", "8002")),
        help="Bind port",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    app.state.model_name = args.model
    app.state.device = args.device

    logger.info("Starting OCR API Server on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
