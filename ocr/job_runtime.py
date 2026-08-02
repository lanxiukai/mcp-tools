"""Persistent runtime state and deterministic discovery for durable OCR jobs."""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError

from ocr.job_files import atomic_write_text
from ocr.job_manifest import ChunkManifest, ChunkStatus, JobId, JobManifest
from ocr.job_store import JobStore


_RUNTIME_FILENAME: Final = "runtime.json"


class JobStatus(StrEnum):
    """Job-level lifecycle state maintained beside the durable manifest."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobRuntime(BaseModel):
    """Validated mutable coordination metadata for one durable job."""

    model_config = ConfigDict(frozen=True)

    status: JobStatus
    error: str | None = None
    completed_at: float | None = None


class JobRuntimeStore:
    """Persists scheduler-only lifecycle metadata without changing manifests."""

    def __init__(self, root: Path, job_store: JobStore) -> None:
        self._root = root
        self._job_store = job_store

    def recover_all(self, now: float) -> tuple[JobManifest, ...]:
        """Recover resumable manifests in durable input-creation order."""
        recovered_manifests: list[JobManifest] = []
        for manifest in self.ordered_manifests():
            recovered = self._job_store.recover(manifest.job_id)
            runtime = self.load(recovered)
            if _all_chunks_completed(recovered):
                completed_at = _completed_at_after_recovery(runtime, now)
                next_runtime = JobRuntime(status=JobStatus.COMPLETED, completed_at=completed_at)
            else:
                next_runtime = JobRuntime(status=JobStatus.QUEUED)
            self.write(recovered.job_id, next_runtime)
            recovered_manifests.append(recovered)
        return tuple(recovered_manifests)

    def ordered_manifests(self) -> tuple[JobManifest, ...]:
        """Load manifests sorted by their immutable staged-input creation time."""
        if not self._root.is_dir():
            return ()
        sortable: list[tuple[int, str, JobManifest]] = []
        for directory in self._root.iterdir():
            manifest_path = directory / "manifest.json"
            if directory.is_dir() and manifest_path.is_file():
                manifest = self._job_store.load(JobId(directory.name))
                input_path = self._job_store.job_directory(manifest.job_id) / manifest.input_path
                sortable.append((input_path.stat().st_mtime_ns, str(manifest.job_id), manifest))
        return tuple(manifest for _, _, manifest in sorted(sortable))

    def load(self, manifest: JobManifest) -> JobRuntime:
        """Load runtime metadata or derive safe state when it is absent or corrupt."""
        try:
            payload = self._runtime_path(manifest.job_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return _initial_runtime(manifest)
        try:
            return JobRuntime.model_validate_json(payload)
        except ValidationError:
            return _initial_runtime(manifest)

    def write(self, job_id: JobId, runtime: JobRuntime) -> None:
        """Atomically publish one scheduler lifecycle transition."""
        atomic_write_text(self._runtime_path(job_id), runtime.model_dump_json(indent=2))

    def next_queued_manifest(self) -> JobManifest | None:
        """Return the oldest runnable job while leaving failed work resumable on restart."""
        for manifest in self.ordered_manifests():
            runtime = self.load(manifest)
            match runtime.status:
                case JobStatus.QUEUED:
                    if _has_pending_chunk(manifest):
                        return manifest
                case JobStatus.RUNNING | JobStatus.COMPLETED | JobStatus.FAILED:
                    continue
                case unreachable:
                    assert_never(unreachable)
        return None

    def queue_size(self) -> int:
        """Count durable pending chunks as bounded queue entries."""
        return sum(_has_pending_chunk(manifest) for manifest in self.ordered_manifests())

    def is_busy(self) -> bool:
        """Report whether queued or running work should block idle shutdown."""
        for manifest in self.ordered_manifests():
            match self.load(manifest).status:
                case JobStatus.QUEUED | JobStatus.RUNNING:
                    return True
                case JobStatus.COMPLETED | JobStatus.FAILED:
                    continue
                case unreachable:
                    assert_never(unreachable)
        return False

    def purge_completed(self, *, now: float, ttl_seconds: float) -> tuple[JobId, ...]:
        """Delete only terminal completed jobs whose recorded completion time expired."""
        removed: list[JobId] = []
        for manifest in self.ordered_manifests():
            runtime = self.load(manifest)
            match runtime.status:
                case JobStatus.COMPLETED:
                    if (
                        runtime.completed_at is not None
                        and _all_chunks_completed(manifest)
                        and now - runtime.completed_at >= ttl_seconds
                    ):
                        shutil.rmtree(self._job_store.job_directory(manifest.job_id))
                        removed.append(manifest.job_id)
                case JobStatus.QUEUED | JobStatus.RUNNING | JobStatus.FAILED:
                    continue
                case unreachable:
                    assert_never(unreachable)
        return tuple(removed)

    def _runtime_path(self, job_id: JobId) -> Path:
        return self._job_store.job_directory(job_id) / _RUNTIME_FILENAME


def _completed_at_after_recovery(runtime: JobRuntime, now: float) -> float:
    match runtime.status:
        case JobStatus.COMPLETED:
            return runtime.completed_at if runtime.completed_at is not None else now
        case JobStatus.QUEUED | JobStatus.RUNNING | JobStatus.FAILED:
            return now
        case unreachable:
            assert_never(unreachable)


def _initial_runtime(manifest: JobManifest) -> JobRuntime:
    if _all_chunks_completed(manifest):
        return JobRuntime(status=JobStatus.COMPLETED)
    if _has_running_chunk(manifest):
        return JobRuntime(status=JobStatus.RUNNING)
    return JobRuntime(status=JobStatus.QUEUED)


def _all_chunks_completed(manifest: JobManifest) -> bool:
    return all(_chunk_is_completed(chunk) for chunk in manifest.chunks)


def _has_pending_chunk(manifest: JobManifest) -> bool:
    return any(_chunk_is_pending(chunk) for chunk in manifest.chunks)


def _has_running_chunk(manifest: JobManifest) -> bool:
    return any(_chunk_is_running(chunk) for chunk in manifest.chunks)


def _chunk_is_completed(chunk: ChunkManifest) -> bool:
    match chunk.status:
        case ChunkStatus.COMPLETED:
            return True
        case ChunkStatus.PENDING | ChunkStatus.RUNNING:
            return False
        case unreachable:
            assert_never(unreachable)


def _chunk_is_pending(chunk: ChunkManifest) -> bool:
    match chunk.status:
        case ChunkStatus.PENDING:
            return True
        case ChunkStatus.RUNNING | ChunkStatus.COMPLETED:
            return False
        case unreachable:
            assert_never(unreachable)


def _chunk_is_running(chunk: ChunkManifest) -> bool:
    match chunk.status:
        case ChunkStatus.RUNNING:
            return True
        case ChunkStatus.PENDING | ChunkStatus.COMPLETED:
            return False
        case unreachable:
            assert_never(unreachable)
