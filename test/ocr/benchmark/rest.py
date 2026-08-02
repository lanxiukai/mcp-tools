"""REST execution flow for staged OCR benchmark jobs."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ocr.pdf_staging import StagedChunk
from test.ocr.benchmark.plan import PageResult
from test.ocr.benchmark.protocol import (
    HttpReply,
    JsonObject,
    OcrResult,
    Progress,
    RestFailure,
    Submission,
    decode_reply,
    optional_number,
    progress_from_reply,
    result_from_reply,
    submission_from_reply,
)
from test.ocr.benchmark.runner import JobMetrics

_MIB_PER_GIB: Final = 1024.0


@dataclass(frozen=True, slots=True)
class GpuSample:
    """Best-effort memory sample from the server health endpoint."""

    elapsed_seconds: float
    allocated_mb: float | None
    reserved_mb: float | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ChunkOutcome:
    """One completed, failed, or timed-out staged OCR submission."""

    chunk: StagedChunk
    status: str
    error: str | None
    pages: tuple[PageResult, ...]
    metrics: JobMetrics
    gpu_samples: tuple[GpuSample, ...]


class OcrBenchmarkClient(Protocol):
    """Operations needed to execute one staged OCR benchmark chunk."""

    def submit(self, pdf_path: Path) -> Submission | RestFailure: ...

    def status(self, job_id: str) -> Progress | RestFailure: ...

    def result(self, job_id: str) -> OcrResult | RestFailure: ...

    def gpu_sample(self, elapsed_seconds: float) -> GpuSample: ...


class OcrRestClient:
    """Synchronous standard-library client for the existing OCR REST API."""

    def __init__(self, server_url: str, timeout_seconds: float) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def submit(self, pdf_path: Path) -> Submission | RestFailure:
        """Upload one staged PDF to ``/v1/ocr/submit``."""
        boundary = f"----ocr-benchmark-{uuid.uuid4().hex}"
        try:
            file_bytes = pdf_path.read_bytes()
        except OSError as error:
            return RestFailure(message=str(error))
        reply = self._request(
            path="/v1/ocr/submit",
            data=_multipart_body(boundary, pdf_path.name, file_bytes),
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return submission_from_reply(reply)

    def status(self, job_id: str) -> Progress | RestFailure:
        """Fetch one job status and page-progress counter."""
        return progress_from_reply(self._request(path=f"/v1/ocr/jobs/{job_id}"))

    def result(self, job_id: str) -> OcrResult | RestFailure:
        """Fetch completed OCR pages from the result endpoint."""
        return result_from_reply(self._request(path=f"/v1/ocr/jobs/{job_id}/result"))

    def gpu_sample(self, elapsed_seconds: float) -> GpuSample:
        """Sample health counters without treating absent GPU fields as a job failure."""
        reply = self._request(path="/health")
        if reply.error is not None or reply.payload is None:
            return GpuSample(elapsed_seconds, None, None, reply.error or "health response lacks JSON")
        return GpuSample(
            elapsed_seconds=elapsed_seconds,
            allocated_mb=_health_memory_mib(
                reply.payload,
                "gpu_memory_allocated_mb",
                "memory_allocated_gb",
            ),
            reserved_mb=_health_memory_mib(
                reply.payload,
                "gpu_memory_reserved_mb",
                "memory_reserved_gb",
            ),
            error=None,
        )

    def _request(
        self,
        *,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpReply:
        headers = {} if content_type is None else {"Content-Type": content_type}
        request = Request(url=f"{self._server_url}{path}", data=data, headers=headers)
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                return decode_reply(response.status, response.read())
        except HTTPError as error:
            return decode_reply(error.code, error.read(), fallback_error=str(error))
        except (TimeoutError, URLError, OSError) as error:
            return HttpReply(status_code=0, payload=None, error=str(error))


def execute_staged_chunk(
    chunk: StagedChunk,
    *,
    client: OcrBenchmarkClient,
    timeout_seconds: float,
    sample_interval_seconds: float,
) -> ChunkOutcome:
    """Submit, poll, retrieve, and map one staged subset without masking errors."""
    started = time.monotonic()
    submitted = client.submit(chunk.pdf_path)
    submitted_at = time.monotonic()
    if isinstance(submitted, RestFailure):
        return _failed_outcome(chunk, started, submitted_at, submitted.message, ())
    samples = [client.gpu_sample(submitted_at - started)]
    first_progress_at: float | None = None
    deadline = started + timeout_seconds
    while time.monotonic() <= deadline:
        observed = client.status(submitted.job_id)
        observed_at = time.monotonic()
        samples.append(client.gpu_sample(observed_at - started))
        if isinstance(observed, RestFailure):
            return _failed_outcome(chunk, started, submitted_at, observed.message, tuple(samples))
        if observed.current > 0 and first_progress_at is None:
            first_progress_at = observed_at
        if observed.status == "failed":
            return _failed_outcome(
                chunk,
                started,
                submitted_at,
                observed.error or "server marked the job failed",
                tuple(samples),
            )
        if observed.status == "completed":
            result = client.result(submitted.job_id)
            retrieved_at = time.monotonic()
            samples.append(client.gpu_sample(retrieved_at - started))
            if isinstance(result, RestFailure):
                return _failed_outcome(chunk, started, submitted_at, result.message, tuple(samples))
            pages = _map_result_pages(chunk, result)
            if pages is None:
                return _failed_outcome(
                    chunk,
                    started,
                    submitted_at,
                    "result pages do not match staged source indexes",
                    tuple(samples),
                )
            return ChunkOutcome(
                chunk=chunk,
                status="completed",
                error=None,
                pages=pages,
                metrics=_metrics(started, submitted_at, first_progress_at, observed_at, retrieved_at),
                gpu_samples=tuple(samples),
            )
        time.sleep(sample_interval_seconds)
    return _failed_outcome(chunk, started, submitted_at, "benchmark timeout", tuple(samples))


def _failed_outcome(
    chunk: StagedChunk,
    started: float,
    submitted_at: float,
    error: str,
    samples: tuple[GpuSample, ...],
) -> ChunkOutcome:
    ended = time.monotonic()
    return ChunkOutcome(
        chunk=chunk,
        status="failed",
        error=error,
        pages=(),
        metrics=_metrics(started, submitted_at, None, ended, ended),
        gpu_samples=samples,
    )


def _metrics(
    started: float,
    submitted_at: float,
    first_progress_at: float | None,
    completion_at: float,
    retrieved_at: float,
) -> JobMetrics:
    progress_start = first_progress_at or completion_at
    return JobMetrics(
        submit_seconds=submitted_at - started,
        queue_seconds=progress_start - submitted_at,
        progress_seconds=completion_at - progress_start,
        completion_seconds=completion_at - submitted_at,
        retrieval_seconds=retrieved_at - completion_at,
        elapsed_seconds=retrieved_at - started,
    )


def _map_result_pages(chunk: StagedChunk, result: OcrResult) -> tuple[PageResult, ...] | None:
    if result.page_count != len(chunk.plan.source_pages) or len(result.pages) != result.page_count:
        return None
    expected_indexes = tuple(range(result.page_count))
    returned_indexes = tuple(page.page_index for page in result.pages)
    if returned_indexes != expected_indexes:
        return None
    return tuple(
        PageResult(
            source_page=source_page,
            markdown=result_page.markdown,
            returned_page_index=result_page.page_index,
        )
        for source_page, result_page in zip(chunk.plan.source_pages, result.pages, strict=True)
    )


def _multipart_body(boundary: str, filename: str, file_bytes: bytes) -> bytes:
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    return prefix + file_bytes + f"\r\n--{boundary}--\r\n".encode()


def _health_memory_mib(
    payload: JsonObject,
    legacy_mib_field: str,
    gpu_info_gib_field: str,
) -> float | None:
    legacy_mib = optional_number(payload, legacy_mib_field)
    if legacy_mib is not None:
        return legacy_mib
    gpu_info = payload.get("gpu_info")
    if not isinstance(gpu_info, dict):
        return None
    gib = optional_number(gpu_info, gpu_info_gib_field)
    return gib * _MIB_PER_GIB if gib is not None else None
