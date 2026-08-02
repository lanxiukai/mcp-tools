"""CPU-only unit tests for the OCR REST benchmark harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from threading import Barrier, Lock
from tempfile import TemporaryDirectory
from unittest import TestCase

from ocr.pdf_staging import plan_chunks, stage_chunk_pdfs
from test.ocr.benchmark.plan import (
    PageResult,
    parse_page_ranges,
    reassemble_pages,
)
from test.ocr.benchmark.protocol import HttpReply
from test.ocr.benchmark.rest import OcrRestClient
from test.ocr.benchmark.runner import JobMetrics, run_bounded


@dataclass(slots=True)
class _ConcurrentProbe:
    """Mutable test double that records active client work."""

    barrier: Barrier = field(default_factory=lambda: Barrier(2))
    lock: Lock = field(default_factory=Lock)
    active: int = 0
    peak: int = 0

    def __call__(self, chunk_index: int) -> int:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.barrier.wait(timeout=2)
        with self.lock:
            self.active -= 1
        return chunk_index


class _ObservedHealthClient(OcrRestClient):
    def _request(
        self,
        *,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpReply:
        return HttpReply(
            status_code=200,
            payload={
                "status": "ok",
                "gpu_info": {
                    "memory_allocated_gb": 2.07,
                    "memory_reserved_gb": 3.86,
                },
            },
            error=None,
        )


class TestBenchmark(TestCase):
    def test_plan_chunks_covers_selected_pages_once(self) -> None:
        """Given sparse ranges, when chunked, then every selected source page occurs once."""
        # Given
        pages = parse_page_ranges("1-3,5,7-9", total_pages=9)

        # When
        chunks = plan_chunks(pages, pages_per_job=2)

        # Then
        self.assertEqual(tuple(page for chunk in chunks for page in chunk.source_pages), pages)
        self.assertEqual(len({page for chunk in chunks for page in chunk.source_pages}), len(pages))

    def test_reassemble_pages_preserves_source_page_order(self) -> None:
        """Given out-of-order job results, when assembled, then source-page order is restored."""
        # Given
        page_results = (
            PageResult(source_page=3, markdown="three"),
            PageResult(source_page=1, markdown="one"),
            PageResult(source_page=2, markdown="two"),
        )

        # When
        assembled = reassemble_pages(page_results)

        # Then
        self.assertEqual(tuple(page.source_page for page in assembled), (1, 2, 3))
        self.assertEqual(tuple(page.markdown for page in assembled), ("one", "two", "three"))

    def test_staged_chunk_pdfs_preserve_source_fixture(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_pdf = root / "source.pdf"
            fitz = import_module("fitz")
            with fitz.open() as source:
                for _ in range(3):
                    source.new_page()
                source.save(source_pdf)
            original_bytes = source_pdf.read_bytes()
            chunks = plan_chunks((1, 3), pages_per_job=1)

            # When
            staged = stage_chunk_pdfs(source_pdf, chunks, root / "staged")

            # Then
            self.assertEqual(source_pdf.read_bytes(), original_bytes)
            self.assertEqual(tuple(item.plan.source_pages for item in staged), ((1,), (3,)))
            with fitz.open(staged[1].pdf_path) as subset:
                self.assertEqual(len(subset), 1)

    def test_job_metrics_emits_required_timing_fields(self) -> None:
        """Given a completed job, when serialized, then all timing phases are present."""
        # Given
        metrics = JobMetrics(
            submit_seconds=0.1,
            queue_seconds=0.2,
            progress_seconds=0.3,
            completion_seconds=0.4,
            retrieval_seconds=0.5,
            elapsed_seconds=1.5,
        )

        # When
        record = metrics.as_record()

        # Then
        self.assertTrue(
            {
                "submit_seconds",
                "queue_seconds",
                "progress_seconds",
                "completion_seconds",
                "retrieval_seconds",
                "elapsed_seconds",
            } <= record.keys()
        )

    def test_gpu_sample_converts_observed_nested_gib_fields_to_mib(self) -> None:
        # Given
        client = _ObservedHealthClient("http://unused", timeout_seconds=1.0)

        # When
        sample = client.gpu_sample(elapsed_seconds=0.25)

        # Then
        allocated_mb = sample.allocated_mb
        reserved_mb = sample.reserved_mb
        if allocated_mb is None or reserved_mb is None:
            self.fail("observed nested GPU fields were not converted to MiB")
        self.assertAlmostEqual(allocated_mb, 2.07 * 1024)
        self.assertAlmostEqual(reserved_mb, 3.86 * 1024)
        self.assertIsNone(sample.error)

    def test_run_bounded_never_exceeds_configured_client_concurrency(self) -> None:
        """Given six fake jobs, when limited to two, then no third job starts concurrently."""
        # Given
        probe = _ConcurrentProbe()

        # When
        completed = run_bounded((0, 1, 2, 3, 4, 5), concurrency=2, execute=probe)

        # Then
        self.assertEqual(completed, (0, 1, 2, 3, 4, 5))
        self.assertEqual(probe.peak, 2)
