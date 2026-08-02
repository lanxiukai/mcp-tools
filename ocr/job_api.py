"""Typed artifact-first response models for durable OCR REST jobs."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ocr.job_runtime import JobStatus
from ocr.job_scheduler import ArtifactReference, ArtifactResult, JobSnapshot


class SchedulerBusy(Protocol):
    """Small server-lifecycle contract needed by the idle monitor."""

    def is_busy(self) -> bool:
        """Report queued or running durable OCR work."""
        ...


class ArtifactResponse(BaseModel):
    """One durable Markdown artifact location and integrity metadata."""

    model_config = ConfigDict(frozen=True)

    chunk_index: int
    source_pages: tuple[int, ...]
    path: str
    sha256: str | None


class JobProgressResponse(BaseModel):
    """Completed source pages relative to the durable manifest's total pages."""

    model_config = ConfigDict(frozen=True)

    current: int
    total: int


class JobStatusResponse(BaseModel):
    """Artifact metadata and job state without embedded OCR Markdown."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    status: JobStatus
    progress: JobProgressResponse
    filename: str
    error: str | None
    artifacts: tuple[ArtifactResponse, ...]


class JobSubmissionResponse(BaseModel):
    """Immediate durable queue acknowledgement with artifact destinations."""

    model_config = ConfigDict(frozen=True)

    success: bool = True
    job_id: str
    status: JobStatus
    filename: str
    total_pages: int
    artifacts: tuple[ArtifactResponse, ...]


class JobResultResponse(BaseModel):
    """Terminal async artifact response with no Markdown body."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    status: JobStatus = JobStatus.COMPLETED
    page_count: int
    artifacts: tuple[ArtifactResponse, ...]


def status_response(snapshot: JobSnapshot) -> JobStatusResponse:
    """Map a durable scheduler snapshot to the public status response."""
    return JobStatusResponse(
        job_id=str(snapshot.job_id),
        status=snapshot.status,
        progress=JobProgressResponse(current=snapshot.completed_pages, total=snapshot.page_count),
        filename=snapshot.filename,
        error=snapshot.error,
        artifacts=tuple(_artifact_response(artifact) for artifact in snapshot.artifacts),
    )


def result_response(result: ArtifactResult) -> JobResultResponse:
    """Map completed durable artifacts without loading their Markdown bodies."""
    return JobResultResponse(
        job_id=str(result.job_id),
        page_count=result.page_count,
        artifacts=tuple(_artifact_response(artifact) for artifact in result.artifacts),
    )


def submission_response(snapshot: JobSnapshot) -> JobSubmissionResponse:
    """Map durable submission metadata without reading any Markdown artifacts."""
    return JobSubmissionResponse(
        job_id=str(snapshot.job_id),
        status=snapshot.status,
        filename=snapshot.filename,
        total_pages=snapshot.page_count,
        artifacts=tuple(_artifact_response(artifact) for artifact in snapshot.artifacts),
    )


def is_server_busy(*, active_requests: int, scheduler: SchedulerBusy) -> bool:
    """Prevent idle shutdown while HTTP requests or durable inference work remain."""
    return active_requests > 0 or scheduler.is_busy()


def _artifact_response(artifact: ArtifactReference) -> ArtifactResponse:
    return ArtifactResponse(
        chunk_index=artifact.chunk_index,
        source_pages=artifact.source_pages,
        path=str(artifact.path),
        sha256=artifact.sha256,
    )
