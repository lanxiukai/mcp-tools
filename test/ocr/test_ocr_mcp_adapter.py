from __future__ import annotations

import json
import inspect
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from unittest import TestCase
from unittest.mock import patch

from ocr import ocr_mcp_server


_ARTIFACT = {
    "chunk_index": 0,
    "source_pages": [1],
    "path": "/durable/jobs/job-1/artifacts/chunk-000.md",
    "sha256": "artifact-sha256",
}


class DurableJobHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def do_GET(self) -> None:  # noqa: N802
        self.requests.append(f"GET {self.path}")
        match self.path:
            case "/health":
                self._send_json(
                    {
                        "status": "ok",
                        "model": "PaddlePaddle/PaddleOCR-VL-1.6",
                        "queue": {"queued": 0, "running": 0},
                    }
                )
            case "/v1/ocr/jobs/job-1":
                self._send_json(
                    {
                        "job_id": "job-1",
                        "status": "completed",
                        "progress": {"current": 1, "total": 1},
                        "filename": "scan.png",
                        "error": None,
                        "artifacts": [_ARTIFACT],
                    }
                )
            case "/v1/ocr/jobs/job-1/result":
                self._send_json(
                    {
                        "job_id": "job-1",
                        "status": "completed",
                        "page_count": 1,
                        "artifacts": [_ARTIFACT],
                    }
                )
            case _:
                self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        self.requests.append(f"POST {self.path}")
        content_length = int(self.headers["Content-Length"])
        self.rfile.read(content_length)
        match self.path:
            case "/v1/ocr/submit":
                self._send_json(
                    {
                        "success": True,
                        "job_id": "job-1",
                        "status": "queued",
                        "filename": "scan.png",
                        "total_pages": 1,
                        "artifacts": [],
                    }
                )
            case _:
                self.send_error(404)

    def log_message(self, format: str, *args: str) -> None:
        return None

    def _send_json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class TestDurableMcpAdapter(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), DurableJobHandler)
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        cls._original_host = ocr_mcp_server.OCR_HOST
        cls._original_port = ocr_mcp_server.OCR_PORT
        ocr_mcp_server.OCR_HOST = "127.0.0.1"
        ocr_mcp_server.OCR_PORT = cls._server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        ocr_mcp_server.OCR_HOST = cls._original_host
        ocr_mcp_server.OCR_PORT = cls._original_port
        cls._server.shutdown()
        cls._server.server_close()
        cls._thread.join()

    def setUp(self) -> None:
        DurableJobHandler.requests.clear()

    def test_submit_returns_durable_queue_metadata_when_server_accepts_upload(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            source.write_bytes(b"image")

            # When
            result = ocr_mcp_server.ocr_submit(str(source))

        # Then
        self.assertEqual(
            result,
            {
                "success": True,
                "job_id": "job-1",
                "status": "queued",
                "filename": "scan.png",
                "total_pages": 1,
                "artifacts": [],
            },
        )
        self.assertNotIn("markdown", result)

    def test_tool_signatures_do_not_expose_legacy_inline_result_options(self) -> None:
        # Given
        parse_parameters = inspect.signature(ocr_mcp_server.ocr_document).parameters

        # When
        parameter_names = tuple(parse_parameters)

        # Then
        self.assertEqual(parameter_names, ("file_path",))
        self.assertNotIn("output_format", parameter_names)
        self.assertNotIn("save_markdown", parameter_names)

    def test_wait_returns_result_artifact_metadata_when_job_completes(self) -> None:
        # Given
        job_id = "job-1"

        # When
        result = ocr_mcp_server.ocr_wait(job_id, max_wait=1)

        # Then
        self.assertEqual(
            result,
            {
                "job_id": "job-1",
                "status": "completed",
                "page_count": 1,
                "artifacts": [_ARTIFACT],
            },
        )
        self.assertNotIn("markdown", result)

    def test_wait_initiates_non_blocking_recovery_when_server_is_offline(self) -> None:
        # Given / When
        with patch.object(ocr_mcp_server, "_ensure_ocr_ready", return_value=False) as ensure_ready:
            result = ocr_mcp_server.ocr_wait("recoverable-job", max_wait=1)

        # Then
        ensure_ready.assert_called_once_with()
        self.assertIn("auto-starting", result["error"])
        self.assertIn("recoverable-job", result["error"])

    def test_parse_submits_and_returns_artifacts_without_source_adjacent_markdown(self) -> None:
        # Given
        with TemporaryDirectory() as directory:
            source = Path(directory) / "scan.png"
            source.write_bytes(b"image")

            # When
            result = ocr_mcp_server.ocr_document(str(source))

            # Then
            self.assertEqual(
                result,
                {
                    "job_id": "job-1",
                    "status": "completed",
                    "page_count": 1,
                    "artifacts": [_ARTIFACT],
                },
            )
            self.assertFalse(source.with_suffix(".md").exists())
        self.assertEqual(
            DurableJobHandler.requests,
            [
                "GET /health",
                "POST /v1/ocr/submit",
                "GET /v1/ocr/jobs/job-1",
                "GET /v1/ocr/jobs/job-1/result",
            ],
        )

    def test_status_returns_queue_health_and_artifact_only_job_status(self) -> None:
        # Given
        job_id = "job-1"

        # When
        health = ocr_mcp_server.ocr_status()
        job = ocr_mcp_server.ocr_status(job_id)

        # Then
        self.assertEqual(health["queue"], {"queued": 0, "running": 0})
        self.assertEqual(job["artifacts"], [_ARTIFACT])
        self.assertNotIn("markdown", job)
