from __future__ import annotations

import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.job_scheduler import (
    ChunkSucceeded,
    DurableJobScheduler,
    JobQueueFullError,
    JobSchedulerConfig,
    JobStatus,
)
from ocr.job_store import JobStore


def _write_pdf(path: Path, *, page_count: int) -> None:
    page_objects = tuple(3 + index * 2 for index in range(page_count))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids ["
        + " ".join(f"{page_object} 0 R" for page_object in page_objects)
        + f"] /Count {page_count} >>",
    ]
    for page_object in page_objects:
        objects.extend(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Contents {page_object + 1} 0 R >>",
                "<< /Length 0 >>\nstream\n\nendstream",
            )
        )
    encoded = tuple(
        f"{index} 0 obj\n{value}\nendobj\n".encode("ascii")
        for index, value in enumerate(objects, start=1)
    )
    header = b"%PDF-1.4\n"
    offsets: list[int] = []
    position = len(header)
    for item in encoded:
        offsets.append(position)
        position += len(item)
    xref = b"xref\n0 " + str(len(encoded) + 1).encode("ascii") + b"\n0000000000 65535 f \n"
    xref += b"".join(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets)
    trailer = (
        b"trailer\n<< /Size "
        + str(len(encoded) + 1).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(position).encode("ascii")
        + b"\n%%EOF\n"
    )
    path.write_bytes(header + b"".join(encoded) + xref + trailer)


class BlockingExecutor:
    """Mutable fake that holds its first inference until the test releases it."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.active_inferences = 0
        self.max_active_inferences = 0

    def execute(self, source: Path) -> ChunkSucceeded:
        self.active_inferences += 1
        self.max_active_inferences = max(self.max_active_inferences, self.active_inferences)
        marker = source.read_text(encoding="utf-8")
        self.calls.append(marker)
        if len(self.calls) == 1:
            self.first_started.set()
            self.release_first.wait(timeout=1)
        self.active_inferences -= 1
        return ChunkSucceeded(markdown=f"# {marker}\n")


def _config(root: Path, *, queue_capacity: int = 8, ttl_seconds: float = 3600) -> JobSchedulerConfig:
    return JobSchedulerConfig(root=root, queue_capacity=queue_capacity, ttl_seconds=ttl_seconds)


class TestDurableJobScheduler(TestCase):
    def test_job_store_stages_twenty_four_and_twenty_five_page_boundaries(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            twenty_four = root / "twenty-four.pdf"
            twenty_five = root / "twenty-five.pdf"
            _write_pdf(twenty_four, page_count=24)
            _write_pdf(twenty_five, page_count=25)
            store = JobStore(root / "jobs")

            # When
            exact = store.create(twenty_four)
            overflow = store.create(twenty_five)

            # Then
            self.assertEqual(tuple(chunk.source_pages for chunk in exact.chunks), (tuple(range(1, 25)),))
            self.assertEqual(
                tuple(chunk.source_pages for chunk in overflow.chunks),
                (tuple(range(1, 25)), (25,)),
            )

    def test_worker_processes_jobs_in_fifo_order_with_one_active_inference(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executor = BlockingExecutor()
            scheduler = DurableJobScheduler(_config(root / "jobs"), executor)
            for marker in ("one", "two", "three"):
                (root / f"{marker}.png").write_text(marker, encoding="utf-8")
            scheduler.start()
            try:
                # When
                first = scheduler.submit(root / "one.png")
                self.assertTrue(executor.first_started.wait(timeout=1))
                second = scheduler.submit(root / "two.png")
                third = scheduler.submit(root / "three.png")
                executor.release_first.set()
                for job in (first, second, third):
                    self.assertIs(
                        scheduler.wait_for_terminal(job.job_id, timeout_seconds=1).status,
                        JobStatus.COMPLETED,
                    )

                # Then
                self.assertEqual(executor.calls, ["one", "two", "three"])
                self.assertEqual(executor.max_active_inferences, 1)
            finally:
                scheduler.stop()

    def test_submit_rejects_a_job_when_the_durable_queue_is_full(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            scheduler = DurableJobScheduler(_config(root / "jobs", queue_capacity=2), BlockingExecutor())
            for marker in ("one", "two", "three"):
                (root / f"{marker}.png").write_text(marker, encoding="utf-8")

            # When
            scheduler.submit(root / "one.png")
            scheduler.submit(root / "two.png")

            # Then
            with self.assertRaises(JobQueueFullError):
                scheduler.submit(root / "three.png")
