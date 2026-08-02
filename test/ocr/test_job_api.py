from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.job_api import is_server_busy, result_response
from ocr.job_scheduler import ChunkSucceeded, DurableJobScheduler, JobSchedulerConfig, JobStatus


class ImmediateExecutor:
    """CPU-only fake executor that returns deterministic Markdown."""

    def execute(self, source: Path) -> ChunkSucceeded:
        return ChunkSucceeded(markdown=f"# {source.read_text(encoding='utf-8')}\n")


class TestJobApiModels(TestCase):
    def test_async_result_response_contains_artifacts_without_embedded_markdown(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            scheduler = DurableJobScheduler(
                JobSchedulerConfig(root=root / "jobs"),
                ImmediateExecutor(),
            )
            scheduler.start()
            try:
                submitted = scheduler.submit_and_wait(source, timeout_seconds=1)

                # When
                payload = result_response(scheduler.result(submitted.job_id)).model_dump(mode="json")

                # Then
                self.assertEqual(set(payload), {"job_id", "status", "page_count", "artifacts"})
                self.assertNotIn("markdown", payload)
                self.assertEqual(payload["status"], JobStatus.COMPLETED)
                self.assertEqual(len(payload["artifacts"]), 1)
                self.assertTrue(Path(payload["artifacts"][0]["path"]).is_file())
            finally:
                scheduler.stop()

    def test_idle_predicate_stays_busy_for_queued_or_running_work(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "scan.png"
            source.write_text("scan", encoding="utf-8")
            scheduler = DurableJobScheduler(
                JobSchedulerConfig(root=root / "jobs"),
                ImmediateExecutor(),
            )

            # When
            submitted = scheduler.submit(source)

            # Then
            self.assertTrue(is_server_busy(active_requests=0, scheduler=scheduler))
            self.assertTrue(is_server_busy(active_requests=1, scheduler=scheduler))

            scheduler.start()
            try:
                completed = scheduler.wait_for_terminal(submitted.job_id, timeout_seconds=1)
                self.assertIs(completed.status, JobStatus.COMPLETED)
                self.assertFalse(is_server_busy(active_requests=0, scheduler=scheduler))
            finally:
                scheduler.stop()
