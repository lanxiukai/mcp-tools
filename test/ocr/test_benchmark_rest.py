from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase

from ocr.pdf_staging import ChunkPlan, StagedChunk
from test.ocr.benchmark.protocol import OcrResult, Progress, RestFailure, ReturnedPage, Submission
from test.ocr.benchmark.rest import GpuSample, OcrRestClient, execute_staged_chunk


class _RestFixtureHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, int | str | list[dict[str, int | str]] | dict[str, float]]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802
        self._send_json({"job_id": "job-1", "total_pages": 2})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(
                {
                    "gpu_info": {
                        "memory_allocated_gb": 1.5,
                        "memory_reserved_gb": 2.25,
                    }
                }
            )
            return
        if self.path.endswith("/result"):
            self._send_json(
                {
                    "page_count": 2,
                    "pages": [
                        {"page_index": 0, "markdown": "one"},
                        {"page_index": 1, "markdown": "two"},
                    ],
                }
            )
            return
        self._send_json({"status": "completed", "progress": {"current": 2, "total": 2}})


@dataclass(frozen=True, slots=True)
class _TerminalClient:
    progress: Progress

    def submit(self, pdf_path: Path) -> Submission | RestFailure:
        return Submission(job_id="job-1", total_pages=1)

    def status(self, job_id: str) -> Progress | RestFailure:
        return self.progress

    def result(self, job_id: str) -> OcrResult | RestFailure:
        return OcrResult(page_count=1, pages=(ReturnedPage(0, "page"),))

    def gpu_sample(self, elapsed_seconds: float) -> GpuSample:
        return GpuSample(elapsed_seconds, None, None, None)


class TestBenchmarkRest(TestCase):
    def test_rest_client_handles_submit_status_result_and_nested_gpu_health(self) -> None:
        # Given
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RestFixtureHandler)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with TemporaryDirectory() as directory:
                pdf_path = Path(directory) / "chunk.pdf"
                pdf_path.write_bytes(b"%PDF-test")
                client = OcrRestClient(f"http://127.0.0.1:{server.server_port}", timeout_seconds=1.0)

                # When
                submitted = client.submit(pdf_path)
                progress = client.status("job-1")
                result = client.result("job-1")
                sample = client.gpu_sample(elapsed_seconds=0.25)

                # Then
                if isinstance(submitted, RestFailure):
                    self.fail(submitted.message)
                if isinstance(progress, RestFailure):
                    self.fail(progress.message)
                if isinstance(result, RestFailure):
                    self.fail(result.message)
                self.assertEqual(submitted.job_id, "job-1")
                self.assertEqual(progress.status, "completed")
                self.assertEqual(tuple(page.page_index for page in result.pages), (0, 1))
                self.assertEqual(sample.allocated_mb, 1536.0)
                self.assertEqual(sample.reserved_mb, 2304.0)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_execute_staged_chunk_records_failed_server_outcome(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            chunk = StagedChunk(ChunkPlan(index=1, source_pages=(1,)), Path(directory) / "chunk.pdf")
            client = _TerminalClient(Progress(status="failed", current=0, total=1, error="model failed"))

            # When
            outcome = execute_staged_chunk(chunk, client=client, timeout_seconds=1.0, sample_interval_seconds=1.0)

            # Then
            self.assertEqual(outcome.status, "failed")
            self.assertEqual(outcome.error, "model failed")

    def test_execute_staged_chunk_records_timeout_like_outcome(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            chunk = StagedChunk(ChunkPlan(index=1, source_pages=(1,)), Path(directory) / "chunk.pdf")
            client = _TerminalClient(Progress(status="running", current=0, total=1, error=None))

            # When
            outcome = execute_staged_chunk(chunk, client=client, timeout_seconds=-1.0, sample_interval_seconds=1.0)

            # Then
            self.assertEqual(outcome.status, "failed")
            self.assertEqual(outcome.error, "benchmark timeout")
