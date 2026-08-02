"""Artifact-only MCP frontend for durable model-independent OCR jobs."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final, TypeVar

REPO_DIR: Final = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ValidationError

from ocr.ocr_mcp_protocol import (
    HealthMetadata,
    JobResultMetadata,
    JobStatusMetadata,
    JobSubmissionMetadata,
    JsonObject,
    ServerErrorMetadata,
    model_payload,
)

START_SCRIPT: Final = REPO_DIR / "ocr" / "ocr_start.sh"
OCR_HOST = os.environ.get("OCR_HOST", "127.0.0.1")
OCR_PORT = int(os.environ.get("OCR_PORT", "8002"))

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
_SUPPORTED_SUFFIXES: Final = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".pdf", ".webp"})


mcp = FastMCP(
    name="OCR",
    json_response=True,
    instructions=(
        "Model-independent document OCR via serialized durable jobs. Tools return only "
        "job metadata and ordered Markdown artifact paths; read artifacts separately."
    ),
)


def _health_url() -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/health"


def _submit_url() -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/submit"


def _job_url(job_id: str) -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/jobs/{job_id}"


def _job_result_url(job_id: str) -> str:
    return f"http://{OCR_HOST}:{OCR_PORT}/v1/ocr/jobs/{job_id}/result"


def _check_ocr_health(timeout: float = 3.0) -> bool:
    """Return whether the OCR service health endpoint is available."""
    try:
        with urllib.request.urlopen(urllib.request.Request(_health_url()), timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
        return False


def _stop_competing_servers() -> None:
    asr_start_script = REPO_DIR / "asr" / "qwen3_asr_start.sh"
    if not asr_start_script.exists():
        return
    subprocess.run(
        ["bash", str(asr_start_script), "stop"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    time.sleep(1)


def _try_start_ocr_server() -> bool:
    if _check_ocr_health():
        return True
    _stop_competing_servers()
    if not START_SCRIPT.exists():
        sys.stderr.write(f"[ocr_mcp] Start script not found: {START_SCRIPT}\n")
        return False
    sys.stderr.write(f"[ocr_mcp] Starting OCR server in background: {START_SCRIPT}\n")
    subprocess.Popen(
        ["bash", str(START_SCRIPT), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            **os.environ,
            "OCR_HOST": OCR_HOST,
            "OCR_PORT": str(OCR_PORT),
        },
    )
    return True


def _ensure_ocr_ready() -> bool:
    """Initiate non-blocking server startup and report whether it is ready now."""
    if _check_ocr_health():
        return True
    _stop_competing_servers()
    if _check_ocr_health():
        return True
    sys.stderr.write("[ocr_mcp] OCR server not running, starting in background...\n")
    _try_start_ocr_server()
    return False


def _server_error_payload(error: urllib.error.HTTPError) -> JsonObject:
    """Preserve a structured server error body instead of replacing it with a string."""
    try:
        return model_payload(ServerErrorMetadata.model_validate_json(error.read()))
    except ValidationError:
        return {"error": f"OCR server returned HTTP {error.code}: {error.reason}"}


def _request_response(
    request: urllib.request.Request,
    timeout: float,
    response_type: type[ResponseModel],
) -> ResponseModel | JsonObject:
    """Request and validate one durable API response without reading artifacts."""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_response = response.read()
    except urllib.error.HTTPError as error:
        return _server_error_payload(error)
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return {"error": f"OCR API request failed: {error}"}

    try:
        return response_type.model_validate_json(raw_response)
    except ValidationError as error:
        return {"error": f"OCR server returned an invalid response: {error}"}


def _request_json(request: urllib.request.Request, timeout: float) -> JsonObject:
    """Request unstructured health metadata while preserving JSON fields."""
    response = _request_response(request, timeout, HealthMetadata)
    match response:
        case HealthMetadata() as health:
            return model_payload(health)
        case dict() as error:
            return error


def _multipart_upload(path: Path) -> tuple[bytes, str]:
    """Build the durable submit endpoint's multipart file body without local writes."""
    boundary = "----OCRSubmitBoundary"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    return body, boundary


def _submit_file(file_path: str) -> JsonObject:
    """Submit an image or PDF to the only OCR inference path: durable jobs."""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a regular file: {file_path}"}
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
        return {"error": f"Unsupported file type: {path.suffix.lower()}. Supported: {supported}"}
    try:
        body, boundary = _multipart_upload(path)
    except OSError as error:
        return {"error": f"Unable to read source file: {error}"}

    response = _request_response(
        urllib.request.Request(
            _submit_url(),
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        ),
        timeout=30,
        response_type=JobSubmissionMetadata,
    )
    match response:
        case JobSubmissionMetadata() as submission:
            return model_payload(submission)
        case dict() as error:
            return error


def _poll_job(job_id: str, max_wait: int = 3600, poll_interval: int = 3) -> JsonObject:
    """Poll durable job state and return terminal artifact metadata only."""
    waited = 0
    last_completed_pages = -1
    while waited < max_wait:
        response = _request_response(
            urllib.request.Request(_job_url(job_id)),
            timeout=5,
            response_type=JobStatusMetadata,
        )
        match response:
            case dict() as error:
                return error
            case JobStatusMetadata() as job:
                if job.progress.current != last_completed_pages and job.progress.total > 0:
                    sys.stderr.write(
                        f"[ocr_mcp] Job {job_id}: page {job.progress.current}/{job.progress.total} "
                        f"(elapsed {waited}s)\n"
                    )
                    last_completed_pages = job.progress.current
                match job.status:
                    case "completed":
                        result = _request_response(
                            urllib.request.Request(_job_result_url(job_id)),
                            timeout=30,
                            response_type=JobResultMetadata,
                        )
                        match result:
                            case JobResultMetadata() as completed:
                                return model_payload(completed)
                            case dict() as error:
                                return error
                    case "failed":
                        return model_payload(job)
                    case _:
                        time.sleep(poll_interval)
                        waited += poll_interval
    return {"error": f"Job {job_id} timed out after {max_wait}s"}


def _starting_error(retry_detail: str) -> JsonObject:
    """Return the stable error payload used while non-blocking startup completes."""
    return {"error": f"OCR server is auto-starting (model loading, ~30s). {retry_detail}"}


@mcp.tool()
def ocr_document(file_path: str) -> JsonObject:
    """Submit and wait for OCR, returning only durable Markdown artifact metadata.

    Images and PDFs use the same serialized server job worker. Artifact files are
    never downloaded or copied next to the input document.
    """
    if not _ensure_ocr_ready():
        return _starting_error("Please wait 30 seconds and retry the same call.")
    submitted = _submit_file(file_path)
    match submitted:
        case {"job_id": str(job_id)}:
            sys.stderr.write(f"[ocr_mcp] Job {job_id} submitted, polling...\n")
            return _poll_job(job_id)
        case _:
            return submitted


@mcp.tool()
def ocr_submit(file_path: str) -> JsonObject:
    """Immediately submit OCR work and return durable queue metadata only."""
    if not _ensure_ocr_ready():
        return _starting_error("Please wait 30 seconds and retry.")
    return _submit_file(file_path)


@mcp.tool()
def ocr_wait(job_id: str, max_wait: int = 1800) -> JsonObject:
    """Wait for a durable OCR job and return terminal artifact metadata only."""
    if not _ensure_ocr_ready():
        return _starting_error(f"Retry ocr_wait() with the same job_id ({job_id}).")
    return _poll_job(job_id, max_wait=max_wait)


@mcp.tool()
def ocr_status(job_id: str = "") -> JsonObject:
    """Return queue-aware server health or artifact-only metadata for one job."""
    if job_id:
        response = _request_response(
            urllib.request.Request(_job_url(job_id)),
            timeout=5,
            response_type=JobStatusMetadata,
        )
        match response:
            case JobStatusMetadata() as job:
                return model_payload(job)
            case dict() as error:
                return error

    if not _check_ocr_health(timeout=2.0):
        return {
            "status": "offline",
            "message": "OCR server is not running. Call ocr_document() to auto-start it.",
        }
    return _request_json(urllib.request.Request(_health_url()), timeout=5.0)


if __name__ == "__main__":
    mcp.run(transport="stdio")
