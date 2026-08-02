from __future__ import annotations

import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ocr.job_manifest import ChunkStatus
from ocr.job_runtime import JobRuntime, JobRuntimeStore
from ocr.job_scheduler import (
    ChunkFailed,
    ChunkSucceeded,
    DurableJobScheduler,
    JobRootInUseError,
    JobSchedulerConfig,
    JobStatus,
)
from ocr.job_store import JobStore


class OutcomeExecutor:
    def __init__(self, outcomes: list[ChunkSucceeded | ChunkFailed]) -> None:
        self._outcomes = outcomes
        self.calls: list[str] = []

    def execute(self, source: Path) -> ChunkSucceeded | ChunkFailed:
        self.calls.append(source.read_text(encoding="utf-8"))
        return self._outcomes.pop(0)


class RaisingExecutor:
    def execute(self, source: Path) -> ChunkSucceeded:
        raise RuntimeError("fake model exception")


def _config(root: Path, *, ttl_seconds: float = 3600) -> JobSchedulerConfig:
    return JobSchedulerConfig(root=root, ttl_seconds=ttl_seconds)


class TestDurableJobRecovery(TestCase):
    def test_restart_recovers_pending_work_in_creation_order_and_skips_verified_chunks(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs")
            for marker in ("done", "first", "second"):
                (root / f"{marker}.png").write_text(marker, encoding="utf-8")
            done = store.create(root / "done.png")
            first = store.create(root / "first.png")
            second = store.create(root / "second.png")
            for sequence, manifest in enumerate((done, first, second), start=1):
                input_path = store.job_directory(manifest.job_id) / manifest.input_path
                timestamp = sequence * 1_000_000_000
                os.utime(input_path, ns=(timestamp, timestamp))
            store.complete_chunk(done.job_id, chunk_index=1, markdown="# durable\n")
            store.mark_running(first.job_id, chunk_index=1)
            executor = OutcomeExecutor([ChunkSucceeded("# first\n"), ChunkSucceeded("# second\n")])
            scheduler = DurableJobScheduler(_config(root / "jobs"), executor)

            # When
            scheduler.start()
            try:
                self.assertIs(
                    scheduler.wait_for_terminal(first.job_id, timeout_seconds=1).status,
                    JobStatus.COMPLETED,
                )
                self.assertIs(
                    scheduler.wait_for_terminal(second.job_id, timeout_seconds=1).status,
                    JobStatus.COMPLETED,
                )

                # Then
                self.assertEqual(executor.calls, ["first", "second"])
                self.assertIs(store.load(done.job_id).chunks[0].status, ChunkStatus.COMPLETED)
            finally:
                scheduler.stop()

    def test_restart_recovers_empty_and_failed_chunks_without_marking_them_complete(self) -> None:
        for outcome in (ChunkSucceeded(""), ChunkFailed("fake model failure")):
            with self.subTest(outcome=outcome):
                self._assert_resumable_after_failure(outcome)

    def test_runtime_exception_returns_running_chunk_to_pending_for_restart(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            store = JobStore(root / "jobs")
            scheduler = DurableJobScheduler(_config(root / "jobs"), RaisingExecutor())
            scheduler.start()
            try:
                # When
                submitted = scheduler.submit(source)
                failed = scheduler.wait_for_terminal(submitted.job_id, timeout_seconds=1)

                # Then
                self.assertIs(failed.status, JobStatus.FAILED)
                self.assertIs(store.load(submitted.job_id).chunks[0].status, ChunkStatus.PENDING)
            finally:
                scheduler.stop()

    def test_ttl_deletes_only_completed_jobs_after_their_completion_time(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            scheduler = DurableJobScheduler(
                _config(root / "jobs", ttl_seconds=1),
                OutcomeExecutor([ChunkSucceeded("# done\n")]),
            )
            scheduler.start()
            try:
                completed = scheduler.submit_and_wait(source, timeout_seconds=1)

                # When
                self.assertIs(completed.status, JobStatus.COMPLETED)
                if completed.completed_at is None:
                    self.fail("completed job has no durable completion time")
                scheduler.cleanup_expired(now=completed.completed_at + 2)

                # Then
                self.assertFalse((root / "jobs" / str(completed.job_id)).exists())
            finally:
                scheduler.stop()

    def test_start_purges_expired_completed_jobs_before_recovery(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            store = JobStore(root / "jobs")
            runtime_store = JobRuntimeStore(root / "jobs", store)
            completed = store.create(source)
            store.complete_chunk(completed.job_id, chunk_index=1, markdown="# done\n")
            runtime_store.write(
                completed.job_id,
                JobRuntime(status=JobStatus.COMPLETED, completed_at=time.time() - 2),
            )
            scheduler = DurableJobScheduler(
                _config(root / "jobs", ttl_seconds=1),
                OutcomeExecutor([]),
            )

            # When
            scheduler.start()
            try:
                # Then
                self.assertFalse((root / "jobs" / str(completed.job_id)).exists())
            finally:
                scheduler.stop()

    def test_cleanup_thread_purges_expired_completed_job_without_requests(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            scheduler = DurableJobScheduler(
                JobSchedulerConfig(
                    root=root / "jobs",
                    ttl_seconds=0.01,
                    cleanup_interval_seconds=0.01,
                ),
                OutcomeExecutor([ChunkSucceeded("# done\n")]),
            )
            scheduler.start()
            try:
                completed = scheduler.submit_and_wait(source, timeout_seconds=1)

                # When
                time.sleep(0.1)

                # Then
                self.assertFalse((root / "jobs" / str(completed.job_id)).exists())
            finally:
                scheduler.stop()

    def test_second_scheduler_for_same_root_fails_until_first_stops(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory) / "jobs"
            first = DurableJobScheduler(_config(root), OutcomeExecutor([]))
            second = DurableJobScheduler(_config(root), OutcomeExecutor([]))
            first.start()
            try:
                # When / Then
                with self.assertRaises(JobRootInUseError):
                    second.start()
            finally:
                first.stop()

            # When
            second.start()
            try:
                # Then
                self.assertTrue(second.is_busy() is False)
            finally:
                second.stop()

    def test_failed_start_releases_job_root_lease(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory) / "jobs"
            failed = DurableJobScheduler(_config(root), OutcomeExecutor([]))
            replacement = DurableJobScheduler(_config(root), OutcomeExecutor([]))

            # When
            with patch.object(failed._runtime_store, "recover_all", side_effect=OSError("fixture failure")):
                with self.assertRaises(OSError):
                    failed.start()

            # Then
            replacement.start()
            replacement.stop()

    def _assert_resumable_after_failure(self, outcome: ChunkSucceeded | ChunkFailed) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            store = JobStore(root / "jobs")
            first_scheduler = DurableJobScheduler(_config(root / "jobs"), OutcomeExecutor([outcome]))
            first_scheduler.start()
            try:
                submitted = first_scheduler.submit(source)
                failed = first_scheduler.wait_for_terminal(submitted.job_id, timeout_seconds=1)
                self.assertIs(failed.status, JobStatus.FAILED)
                self.assertIs(store.load(submitted.job_id).chunks[0].status, ChunkStatus.PENDING)
            finally:
                first_scheduler.stop()

            recovered_executor = OutcomeExecutor([ChunkSucceeded("# recovered\n")])
            recovered_scheduler = DurableJobScheduler(_config(root / "jobs"), recovered_executor)
            recovered_scheduler.start()
            try:
                resumed = recovered_scheduler.wait_for_terminal(submitted.job_id, timeout_seconds=1)
                self.assertIs(resumed.status, JobStatus.COMPLETED)
                self.assertEqual(recovered_executor.calls, ["scan"])
            finally:
                recovered_scheduler.stop()
