"""One-worker durable OCR scheduling over JobStore's staged chunk artifacts."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import assert_never

from ocr.job_manifest import ChunkManifest, JobId, JobManifest
from ocr.job_runtime import JobRuntime, JobRuntimeStore, JobStatus
from ocr.job_scheduler_cleanup import PeriodicJobCleanup
from ocr.job_scheduler_lease import JobRootLease
from ocr.job_store import JobStore
from ocr.job_scheduler_types import (
    ArtifactReference,
    ArtifactResult,
    ChunkExecution,
    ChunkExecutor,
    ChunkFailed,
    ChunkSucceeded,
    JobFailedError,
    JobNotFoundError,
    JobNotReadyError,
    JobQueueFullError,
    JobRootInUseError,
    JobSchedulerConfig,
    JobSnapshot,
    JobWaitTimeoutError,
    completed_page_count,
    first_pending_chunk,
)


class DurableJobScheduler:
    """Owns one non-daemon inference worker and a durable bounded FIFO queue."""

    def __init__(self, config: JobSchedulerConfig, executor: ChunkExecutor) -> None:
        self._config = config
        self._executor = executor
        self._job_store = JobStore(config.root)
        self._runtime_store = JobRuntimeStore(config.root, self._job_store)
        self._lease = JobRootLease(config.root)
        self._condition = threading.Condition()
        self._submission_lock = threading.RLock()
        self._started = False
        self._stopping = False
        self._worker: threading.Thread | None = None
        self._cleanup = PeriodicJobCleanup(config.cleanup_interval_seconds, self.cleanup_expired)

    def start(self) -> None:
        """Recover durable work before starting exactly one inference worker."""
        with self._condition:
            if self._started:
                return
            self._lease.acquire()
            started = False
            try:
                self.cleanup_expired()
                self._runtime_store.recover_all(now=time.time())
                self._stopping = False
                self._worker = threading.Thread(
                    target=self._run_worker,
                    name="ocr-inference-worker",
                    daemon=False,
                )
                self._cleanup.start()
                self._worker.start()
                self._started = True
                started = True
            finally:
                if not started:
                    self._cleanup.stop()
                    self._worker = None
                    self._lease.release()

    def stop(self) -> None:
        """Stop the one worker after any currently running inference completes."""
        with self._condition:
            if not self._started:
                return
            self._stopping = True
            worker = self._worker
            self._condition.notify_all()
        if worker is not None:
            worker.join()
        self._cleanup.stop()
        with self._condition:
            self._worker = None
            self._started = False
            self._lease.release()

    def submit(self, source: Path) -> JobSnapshot:
        """Durably accept one source only when the pending FIFO queue has room."""
        with self._submission_lock:
            self.cleanup_expired()
            if self._runtime_store.queue_size() >= self._config.queue_capacity:
                raise JobQueueFullError(capacity=self._config.queue_capacity)
            manifest = self._job_store.create(source)
            self._runtime_store.write(manifest.job_id, JobRuntime(status=JobStatus.QUEUED))
            snapshot = self._snapshot(manifest)
        with self._condition:
            self._condition.notify_all()
        return snapshot

    def submit_and_wait(self, source: Path, *, timeout_seconds: float | None = None) -> JobSnapshot:
        """Use the durable queue for a legacy synchronous request and await its terminal state."""
        submitted = self.submit(source)
        return self.wait_for_terminal(submitted.job_id, timeout_seconds=timeout_seconds)

    def status(self, job_id: JobId) -> JobSnapshot:
        """Return durable status after pruning only expired completed jobs."""
        self.cleanup_expired()
        return self._snapshot_for_id(job_id)

    def result(self, job_id: JobId) -> ArtifactResult:
        """Return artifact metadata only after all chunks are durably completed."""
        snapshot = self.status(job_id)
        match snapshot.status:
            case JobStatus.COMPLETED:
                return ArtifactResult(
                    job_id=snapshot.job_id,
                    page_count=snapshot.page_count,
                    artifacts=snapshot.artifacts,
                )
            case JobStatus.FAILED:
                raise JobFailedError(job_id=snapshot.job_id, error=snapshot.error or "inference failed")
            case JobStatus.QUEUED | JobStatus.RUNNING:
                raise JobNotReadyError(job_id=snapshot.job_id, status=snapshot.status)
            case unreachable:
                assert_never(unreachable)

    def wait_for_terminal(self, job_id: JobId, *, timeout_seconds: float | None) -> JobSnapshot:
        """Block a compatibility caller while its work still uses the single worker."""
        configured_timeout = 0.0 if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + configured_timeout if timeout_seconds is not None else None
        with self._condition:
            while True:
                snapshot = self._snapshot_for_id(job_id)
                match snapshot.status:
                    case JobStatus.COMPLETED | JobStatus.FAILED:
                        return snapshot
                    case JobStatus.QUEUED | JobStatus.RUNNING:
                        if deadline is None:
                            self._condition.wait()
                            continue
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise JobWaitTimeoutError(job_id=job_id, timeout_seconds=configured_timeout)
                        self._condition.wait(timeout=remaining)
                    case unreachable:
                        assert_never(unreachable)

    def cleanup_expired(self, *, now: float | None = None) -> tuple[JobId, ...]:
        """Remove only completed jobs whose durable completion time exceeded TTL."""
        with self._submission_lock:
            return self._runtime_store.purge_completed(
                now=time.time() if now is None else now,
                ttl_seconds=self._config.ttl_seconds,
            )

    def is_busy(self) -> bool:
        """Expose durable queued/running work to the server idle monitor."""
        return self._runtime_store.is_busy()

    def _run_worker(self) -> None:
        while True:
            manifest = self._wait_for_queued_manifest()
            if manifest is None:
                return
            self._process_manifest(manifest)

    def _wait_for_queued_manifest(self) -> JobManifest | None:
        with self._condition:
            while not self._stopping:
                manifest = self._runtime_store.next_queued_manifest()
                if manifest is not None:
                    return manifest
                self._condition.wait()
        return None

    def _process_manifest(self, manifest: JobManifest) -> None:
        chunk = first_pending_chunk(manifest)
        if chunk is None:
            return
        try:
            running = self._job_store.mark_running(manifest.job_id, chunk_index=chunk.index)
        except OSError as error:
            self._record_failure(manifest, str(error))
            return
        self._runtime_store.write(running.job_id, JobRuntime(status=JobStatus.RUNNING))
        try:
            execution = self._executor.execute(
                self._job_store.job_directory(running.job_id) / chunk.staged_path
            )
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            self._record_failure(running, str(error))
            return
        match execution:
            case ChunkSucceeded(markdown=markdown):
                if markdown.strip():
                    self._complete_chunk(running, chunk, markdown)
                else:
                    self._record_failure(running, "inference produced empty Markdown")
            case ChunkFailed(error=error):
                self._record_failure(running, error)
            case unreachable:
                assert_never(unreachable)

    def _complete_chunk(self, manifest: JobManifest, chunk: ChunkManifest, markdown: str) -> None:
        try:
            completed = self._job_store.complete_chunk(
                manifest.job_id,
                chunk_index=chunk.index,
                markdown=markdown,
            )
        except OSError as error:
            self._record_failure(manifest, str(error))
            return
        if completed_page_count(completed) == completed.page_count:
            runtime = JobRuntime(status=JobStatus.COMPLETED, completed_at=time.time())
        else:
            runtime = JobRuntime(status=JobStatus.QUEUED)
        with self._condition:
            self._runtime_store.write(completed.job_id, runtime)
            self._condition.notify_all()

    def _record_failure(self, manifest: JobManifest, error: str) -> None:
        recovered = self._job_store.recover(manifest.job_id)
        with self._condition:
            self._runtime_store.write(recovered.job_id, JobRuntime(status=JobStatus.FAILED, error=error))
            self._condition.notify_all()

    def _snapshot_for_id(self, job_id: JobId) -> JobSnapshot:
        try:
            manifest = self._job_store.load(job_id)
        except FileNotFoundError as error:
            raise JobNotFoundError(job_id=job_id) from error
        return self._snapshot(manifest)

    def _snapshot(self, manifest: JobManifest) -> JobSnapshot:
        runtime = self._runtime_store.load(manifest)
        directory = self._job_store.job_directory(manifest.job_id)
        artifacts = tuple(
            ArtifactReference(
                chunk_index=chunk.index,
                source_pages=chunk.source_pages,
                path=directory / chunk.artifact_path,
                sha256=chunk.artifact_digest,
            )
            for chunk in manifest.chunks
        )
        return JobSnapshot(
            job_id=manifest.job_id,
            status=runtime.status,
            filename=manifest.input_path.name,
            page_count=manifest.page_count,
            completed_pages=completed_page_count(manifest),
            artifacts=artifacts,
            error=runtime.error,
            completed_at=runtime.completed_at,
        )
