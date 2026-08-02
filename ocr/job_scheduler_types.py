"""Typed scheduler inputs, outcomes, snapshots, and configuration parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias, assert_never

from ocr.job_manifest import ChunkManifest, ChunkStatus, JobId, JobManifest
from ocr.job_runtime import JobStatus


@dataclass(frozen=True, slots=True)
class SchedulerConfigurationError(Exception):
    """Raised when durable scheduler configuration cannot be parsed safely."""

    variable: str
    value: str

    def __str__(self) -> str:
        return f"{self.variable} must be a positive number, got {self.value!r}"


@dataclass(frozen=True, slots=True)
class JobQueueFullError(Exception):
    """Raised before persistence when the durable pending queue reached capacity."""

    capacity: int

    def __str__(self) -> str:
        return f"OCR job queue is full (capacity {self.capacity})"


@dataclass(frozen=True, slots=True)
class JobRootInUseError(Exception):
    """Raised when another scheduler already owns the durable job root."""

    root: Path

    def __str__(self) -> str:
        return f"OCR job root is already in use: {self.root}"


@dataclass(frozen=True, slots=True)
class JobNotFoundError(Exception):
    """Raised when a requested durable job directory does not exist."""

    job_id: JobId

    def __str__(self) -> str:
        return f"OCR job {self.job_id} was not found"


@dataclass(frozen=True, slots=True)
class JobWaitTimeoutError(Exception):
    """Raised when a synchronous compatibility request outlives its wait budget."""

    job_id: JobId
    timeout_seconds: float

    def __str__(self) -> str:
        return f"OCR job {self.job_id} did not finish within {self.timeout_seconds} seconds"


@dataclass(frozen=True, slots=True)
class JobNotReadyError(Exception):
    """Raised when artifact retrieval is requested before terminal completion."""

    job_id: JobId
    status: JobStatus

    def __str__(self) -> str:
        return f"OCR job {self.job_id} is {self.status}, not completed"


@dataclass(frozen=True, slots=True)
class JobFailedError(Exception):
    """Raised when artifact retrieval is requested from a failed resumable job."""

    job_id: JobId
    error: str

    def __str__(self) -> str:
        return f"OCR job {self.job_id} failed: {self.error}"


@dataclass(frozen=True, slots=True)
class JobSchedulerConfig:
    """Environment-derived durable root, queue bound, and completed-job retention."""

    root: Path
    queue_capacity: int = 8
    ttl_seconds: float = 3600
    cleanup_interval_seconds: float = 60

    def __post_init__(self) -> None:
        if self.queue_capacity < 1:
            raise SchedulerConfigurationError("OCR_QUEUE_CAPACITY", str(self.queue_capacity))
        if self.ttl_seconds <= 0:
            raise SchedulerConfigurationError("OCR_JOB_TTL_SECONDS", str(self.ttl_seconds))
        if self.cleanup_interval_seconds <= 0:
            raise SchedulerConfigurationError("cleanup_interval_seconds", str(self.cleanup_interval_seconds))

    @classmethod
    def from_environment(cls) -> JobSchedulerConfig:
        """Read explicit job settings or the XDG state-directory fallback."""
        configured_root = os.environ.get("OCR_JOB_ROOT")
        if configured_root is not None:
            root = Path(configured_root).expanduser()
        else:
            state_home = os.environ.get("XDG_STATE_HOME")
            state_root = Path(state_home).expanduser() if state_home is not None else Path.home() / ".local" / "state"
            root = state_root / "ocr" / "jobs"
        return cls(
            root=root,
            queue_capacity=_positive_int("OCR_QUEUE_CAPACITY", 8),
            ttl_seconds=_positive_float("OCR_JOB_TTL_SECONDS", 3600),
        )


@dataclass(frozen=True, slots=True)
class ChunkSucceeded:
    """One model execution that produced Markdown suitable for durable publication."""

    markdown: str


@dataclass(frozen=True, slots=True)
class ChunkFailed:
    """One model execution that failed without publishing an artifact."""

    error: str


ChunkExecution: TypeAlias = ChunkSucceeded | ChunkFailed


class ChunkExecutor(Protocol):
    """Worker-only inference boundary that never exposes OCRModel to HTTP routes."""

    def execute(self, source: Path) -> ChunkExecution:
        """Run one staged PDF or image chunk and return its typed outcome."""
        ...


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Metadata for one durable Markdown artifact without its Markdown payload."""

    chunk_index: int
    source_pages: tuple[int, ...]
    path: Path
    sha256: str | None


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Current durable job state for HTTP status and synchronous waits."""

    job_id: JobId
    status: JobStatus
    filename: str
    page_count: int
    completed_pages: int
    artifacts: tuple[ArtifactReference, ...]
    error: str | None
    completed_at: float | None


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    """Terminal async result containing artifact locations, not OCR Markdown."""

    job_id: JobId
    page_count: int
    artifacts: tuple[ArtifactReference, ...]


def first_pending_chunk(manifest: JobManifest) -> ChunkManifest | None:
    """Find the first incomplete durable chunk in source-page order."""
    for chunk in manifest.chunks:
        match chunk.status:
            case ChunkStatus.PENDING:
                return chunk
            case ChunkStatus.RUNNING | ChunkStatus.COMPLETED:
                continue
            case unreachable:
                assert_never(unreachable)
    return None


def completed_page_count(manifest: JobManifest) -> int:
    """Count source pages whose chunk artifacts are durably complete."""
    return sum(_completed_source_pages(chunk) for chunk in manifest.chunks)


def _completed_source_pages(chunk: ChunkManifest) -> int:
    match chunk.status:
        case ChunkStatus.COMPLETED:
            return len(chunk.source_pages)
        case ChunkStatus.PENDING | ChunkStatus.RUNNING:
            return 0
        case unreachable:
            assert_never(unreachable)


def _positive_int(variable: str, default: int) -> int:
    raw = os.environ.get(variable)
    raw = str(default) if raw is None else raw
    try:
        value = int(raw)
    except ValueError as error:
        raise SchedulerConfigurationError(variable, raw) from error
    if value < 1:
        raise SchedulerConfigurationError(variable, raw)
    return value


def _positive_float(variable: str, default: float) -> float:
    raw = os.environ.get(variable)
    raw = str(default) if raw is None else raw
    try:
        value = float(raw)
    except ValueError as error:
        raise SchedulerConfigurationError(variable, raw) from error
    if value <= 0:
        raise SchedulerConfigurationError(variable, raw)
    return value
