from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ocr.job_runtime import JobRuntime, JobStatus
from ocr.job_scheduler import ChunkSucceeded, DurableJobScheduler, JobSchedulerConfig
from ocr.job_store import JobStore


class HoldingExecutor:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, source: Path) -> ChunkSucceeded:
        self.started.set()
        self.release.wait(timeout=1)
        return ChunkSucceeded(markdown="# done\n")


class TestJobSchedulerConfig(TestCase):
    def test_environment_uses_explicit_root_then_xdg_fallback_with_queue_and_ttl(self) -> None:
        # Given / When
        with patch.dict(
            os.environ,
            {
                "OCR_JOB_ROOT": "/tmp/explicit-jobs",
                "XDG_STATE_HOME": "/tmp/ignored-state",
                "OCR_QUEUE_CAPACITY": "5",
                "OCR_JOB_TTL_SECONDS": "90",
            },
            clear=True,
        ):
            explicit = JobSchedulerConfig.from_environment()
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/state"}, clear=True):
            fallback = JobSchedulerConfig.from_environment()

        # Then
        self.assertEqual(explicit.root, Path("/tmp/explicit-jobs"))
        self.assertEqual(explicit.queue_capacity, 5)
        self.assertEqual(explicit.ttl_seconds, 90)
        self.assertEqual(fallback.root, Path("/tmp/state/ocr/jobs"))

    def test_cleanup_preserves_running_resumable_job_even_after_ttl_window(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            executor = HoldingExecutor()
            scheduler = DurableJobScheduler(
                JobSchedulerConfig(root=root / "jobs", ttl_seconds=1),
                executor,
            )
            scheduler.start()
            try:
                submitted = scheduler.submit(source)
                self.assertTrue(executor.started.wait(timeout=1))

                # When
                scheduler.cleanup_expired(now=time.time() + 10)

                # Then
                self.assertTrue((root / "jobs" / str(submitted.job_id)).is_dir())
            finally:
                executor.release.set()
                scheduler.stop()

    def test_cleanup_preserves_queued_and_failed_jobs_after_ttl_window(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            queued_source = root / "queued.png"
            failed_source = root / "failed.png"
            queued_source.write_text("queued", encoding="utf-8")
            failed_source.write_text("failed", encoding="utf-8")
            store = JobStore(root / "jobs")
            queued = store.create(queued_source)
            failed = store.create(failed_source)
            scheduler = DurableJobScheduler(
                JobSchedulerConfig(root=root / "jobs", ttl_seconds=1),
                HoldingExecutor(),
            )
            scheduler._runtime_store.write(failed.job_id, JobRuntime(status=JobStatus.FAILED, error="failed"))

            # When
            scheduler.cleanup_expired(now=time.time() + 10)

            # Then
            self.assertTrue((root / "jobs" / str(queued.job_id)).is_dir())
            self.assertTrue((root / "jobs" / str(failed.job_id)).is_dir())
